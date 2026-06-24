"""
CNN 分类数据集构建
基于 GT 标注框裁剪 ROI，构建 4 类缺陷分类数据集
"""

import json
import os
import random
import shutil
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
import torch
from torch.utils.data import Dataset, DataLoader, WeightedRandomSampler
from torchvision import transforms


def imread_unicode(path):
    """读取图像，支持中文路径"""
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


# 类别映射
DEFECT_TO_IDX = {
    "stain": 0,
    "discontinuity": 1,
    "deposit": 2,
    "pore": 3,
}

IDX_TO_DEFECT = {v: k for k, v in DEFECT_TO_IDX.items()}
CNN_CLASSES = ["stain", "discontinuity", "deposit", "pore"]


def extract_rois_from_json(json_path, img_path, target_size=(224, 224)):
    """
    从一张图像的 GT 标注中提取所有缺陷 ROI。

    Args:
        json_path: LabelMe JSON 路径
        img_path: 对应的图像路径
        target_size: ROI 缩放尺寸

    Returns:
        list[dict]: [{"image": PIL.Image, "label": str, "class_idx": int, "bbox": [x1,y1,x2,y2]}, ...]
    """
    if not json_path.exists() or not img_path.exists():
        return []

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    img = imread_unicode(str(img_path))
    if img is None:
        return []
    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    h, w = img.shape[:2]

    rois = []
    for shape in data.get("shapes", []):
        label = shape["label"]
        if label not in DEFECT_TO_IDX:
            continue

        points = shape["points"]
        (x1, y1), (x2, y2) = points[0], points[1]

        # 确保坐标在图像范围内
        x1 = max(0, int(min(x1, x2)))
        y1 = max(0, int(min(y1, y2)))
        x2 = min(w, int(max(x1, x2)))
        y2 = min(h, int(max(y1, y2)))

        if x2 <= x1 or y2 <= y1:
            continue

        # 裁剪 ROI
        roi = img_rgb[y1:y2, x1:x2]
        if roi.size == 0:
            continue

        # 转为 PIL 并缩放
        roi_pil = Image.fromarray(roi).resize(target_size, Image.BILINEAR)

        rois.append({
            "image": roi_pil,
            "label": label,
            "class_idx": DEFECT_TO_IDX[label],
            "bbox": [x1, y1, x2, y2],
        })

    return rois


def build_cnn_dataset(data_dir, output_dir, train_ratio=0.8):
    """
    从原始数据集构建 CNN 分类数据集。

    Args:
        data_dir: 原始数据目录 (data/)
        output_dir: 输出目录 (datasets/cnn/)
        train_ratio: 训练集比例
    """
    output_dir = Path(output_dir)
    random.seed(42)

    # 收集所有 (json_path, img_path) 对
    all_samples = {label: [] for label in DEFECT_TO_IDX}

    for quality in ["high", "low"]:
        defects_dir = Path(data_dir) / quality / "defects"
        imgs_dir = Path(data_dir) / quality / "images"

        if not defects_dir.exists():
            continue

        for json_file in defects_dir.glob("*.json"):
            stem = json_file.stem

            # 查找对应图像
            img_path = None
            for ext in [".jpg", ".bmp", ".png"]:
                candidate = imgs_dir / f"{stem}{ext}"
                if candidate.exists():
                    img_path = candidate
                    break

            if img_path is None:
                continue

            # 检查是否有缺陷标注
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            has_defect = False
            for shape in data.get("shapes", []):
                label = shape["label"]
                if label in DEFECT_TO_IDX:
                    has_defect = True
                    break

            if has_defect:
                all_samples["_pairs"] = all_samples.get("_pairs", [])
                all_samples["_pairs"].append((json_file, img_path))

    # 统计
    print("=== 各类别样本统计 ===")
    pair_count = len(all_samples.get("_pairs", []))
    print(f"  含缺陷的图像数: {pair_count}")

    # 划分训练/验证
    pairs = all_samples.pop("_pairs", [])
    random.shuffle(pairs)
    split = int(len(pairs) * train_ratio)
    train_pairs = pairs[:split]
    val_pairs = pairs[split:]

    print(f"  训练集图像: {len(train_pairs)}, 验证集图像: {len(val_pairs)}")

    # 提取 ROI 并保存
    for split_name, split_pairs in [("train", train_pairs), ("val", val_pairs)]:
        class_counts = {label: 0 for label in DEFECT_TO_IDX}

        for json_path, img_path in split_pairs:
            rois = extract_rois_from_json(json_path, img_path)
            for j, roi in enumerate(rois):
                label = roi["label"]
                out_class_dir = Path(output_dir) / split_name / label
                out_class_dir.mkdir(parents=True, exist_ok=True)
                save_path = out_class_dir / f"{json_path.stem}_{j}.png"
                roi["image"].save(save_path)
                class_counts[label] += 1

        print(f"\n  [{split_name}] 各类别数量:")
        for label, count in class_counts.items():
            print(f"    {label}: {count}")
        print(f"  [{split_name}] 总计: {sum(class_counts.values())} 张")


class WeldDefectDataset(Dataset):
    """4类焊缝缺陷分类 Dataset"""

    def __init__(self, root_dir, transform=None):
        """
        Args:
            root_dir: 数据集根目录 (如 datasets/cnn/train/)
            transform: torchvision transforms
        """
        self.root_dir = Path(root_dir)
        self.transform = transform
        self.samples = []

        for class_name in DEFECT_TO_IDX:
            class_dir = self.root_dir / class_name
            if class_dir.exists():
                for img_path in class_dir.glob("*.png"):
                    self.samples.append({
                        "path": str(img_path),
                        "label": DEFECT_TO_IDX[class_name],
                    })

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        sample = self.samples[idx]
        image = Image.open(sample["path"]).convert("RGB")

        if self.transform:
            image = self.transform(image)

        return image, sample["label"]

    def get_class_weights(self):
        """计算类别权重（用于 WeightedRandomSampler）"""
        label_counts = {}
        for s in self.samples:
            label_counts[s["label"]] = label_counts.get(s["label"], 0) + 1

        total = len(self.samples)
        weights = []
        for s in self.samples:
            weights.append(total / label_counts[s["label"]])

        return torch.tensor(weights, dtype=torch.float)


def get_cnn_transforms(is_train=True, input_size=224):
    """获取 CNN 数据增强 transforms"""
    if is_train:
        return transforms.Compose([
            transforms.RandomHorizontalFlip(p=0.5),
            transforms.RandomVerticalFlip(p=0.3),
            transforms.RandomRotation(degrees=15),
            transforms.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.2, hue=0.05),
            transforms.RandomAffine(degrees=0, translate=(0.1, 0.1), scale=(0.9, 1.1)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])
    else:
        return transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])


def create_dataloaders(cnn_data_dir, batch_size=32, num_workers=2):
    """创建训练和验证 DataLoader（带均衡采样）"""
    train_dataset = WeldDefectDataset(
        Path(cnn_data_dir) / "train",
        transform=get_cnn_transforms(is_train=True),
    )
    val_dataset = WeldDefectDataset(
        Path(cnn_data_dir) / "val",
        transform=get_cnn_transforms(is_train=False),
    )

    # 加权采样处理类别不均衡
    weights = train_dataset.get_class_weights()
    sampler = WeightedRandomSampler(weights, num_samples=len(weights), replacement=True)

    train_loader = DataLoader(
        train_dataset,
        batch_size=batch_size,
        sampler=sampler,
        num_workers=num_workers,
        pin_memory=True,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=batch_size,
        shuffle=False,
        num_workers=num_workers,
        pin_memory=True,
    )

    return train_loader, val_loader, train_dataset, val_dataset


if __name__ == "__main__":
    base = Path(r"c:\Users\hyh\OneDrive\桌面\实训")
    print("=== 构建 CNN 分类数据集 ===\n")
    build_cnn_dataset(
        data_dir=base / "data",
        output_dir=base / "datasets" / "cnn",
        train_ratio=0.8,
    )
    print("\n=== CNN 数据集构建完成 ===")
