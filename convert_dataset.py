"""
数据集格式转换: LabelMe JSON → YOLO txt
将原始6类合并为2类: weld(0), defect(1)
"""

import json
import os
import shutil
import random
import numpy as np
import cv2
from pathlib import Path


def imread_unicode(path):
    """读取图像，支持中文路径"""
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img

# 类别映射: 原始标签 → YOLO class_id
DEFECT_LABEL_MAP = {
    "stain": 1,
    "discontinuity": 1,
    "deposit": 1,
    "pore": 1,
}

WELD_LABEL_MAP = {
    "weld": 0,
    "part_weld": 0,
}

# 4类缺陷名称 (供CNN训练使用)
CNN_CLASSES = ["stain", "discontinuity", "deposit", "pore"]
CNN_CLASS_TO_IDX = {name: i for i, name in enumerate(CNN_CLASSES)}


def convert_labelme_to_yolo(json_path, img_width, img_height):
    """
    读取单个 LabelMe JSON，转换为 YOLO 格式行列表。

    Args:
        json_path: LabelMe JSON 文件路径
        img_width: 图像宽度 (int)
        img_height: 图像高度 (int)

    Returns:
        list[str]: YOLO 格式行 ["class_id cx cy w h", ...]
    """
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    img_width = img_width or data.get("imageWidth", 640)
    img_height = img_height or data.get("imageHeight", 480)

    yolo_lines = []
    for shape in data.get("shapes", []):
        label = shape["label"]
        points = shape["points"]  # [[x1,y1], [x2,y2]]

        # 确定 class_id
        if label in WELD_LABEL_MAP:
            class_id = WELD_LABEL_MAP[label]
        elif label in DEFECT_LABEL_MAP:
            class_id = DEFECT_LABEL_MAP[label]
        else:
            continue  # 跳过未知标签

        # 提取坐标
        (x1, y1), (x2, y2) = points[0], points[1]

        # 计算 YOLO 归一化坐标
        cx = ((min(x1, x2) + max(x1, x2)) / 2) / img_width
        cy = ((min(y1, y2) + max(y1, y2)) / 2) / img_height
        w = abs(x1 - x2) / img_width
        h = abs(y1 - y2) / img_height

        # 裁剪到 [0, 1]
        cx, cy, w, h = max(0, min(1, cx)), max(0, min(1, cy)), max(0, min(1, w)), max(0, min(1, h))

        if w > 0 and h > 0:
            yolo_lines.append(f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

    return yolo_lines


def process_split(quality, image_dir, defects_dir, welds_dir, output_img_dir, output_label_dir, image_list):
    """
    处理一个数据子集（high 或 low），生成 YOLO 标注文件并复制图像。

    Args:
        quality: "high" 或 "low"
        image_dir: 图像目录路径
        defects_dir: 缺陷JSON目录路径
        welds_dir: 焊缝JSON目录路径
        output_img_dir: 输出图像目录
        output_label_dir: 输出标注目录
        image_list: 要处理的图像文件名列表
    """
    os.makedirs(output_img_dir, exist_ok=True)
    os.makedirs(output_label_dir, exist_ok=True)

    for img_name in image_list:
        # 找到对应的图像文件
        img_path = Path(image_dir) / img_name
        if not img_path.exists():
            # 尝试不同扩展名
            for ext in [".jpg", ".bmp", ".png"]:
                stem = Path(img_name).stem
                alt_path = Path(image_dir) / f"{stem}{ext}"
                if alt_path.exists():
                    img_path = alt_path
                    img_name = f"{stem}{ext}"
                    break

        if not img_path.exists():
            print(f"  [SKIP] 图像不存在: {img_name}")
            continue

        # 读取图像尺寸
        img = imread_unicode(str(img_path))
        if img is None:
            print(f"  [SKIP] 无法读取图像: {img_name}")
            continue
        h, w = img.shape[:2]

        # 合并 welds 和 defects 标注
        stem = Path(img_name).stem
        all_lines = []

        # 读取焊缝标注
        weld_json = Path(welds_dir) / f"{stem}.json"
        if weld_json.exists():
            all_lines.extend(convert_labelme_to_yolo(str(weld_json), w, h))

        # 读取缺陷标注
        defect_json = Path(defects_dir) / f"{stem}.json"
        if defect_json.exists():
            all_lines.extend(convert_labelme_to_yolo(str(defect_json), w, h))

        if not all_lines:
            print(f"  [WARN] 无标注: {img_name}")
            continue

        # 写入 YOLO txt
        out_name = Path(img_name).stem + ".txt"
        label_path = Path(output_label_dir) / out_name
        with open(label_path, "w", encoding="utf-8") as f:
            f.write("\n".join(all_lines))

        # 复制图像
        shutil.copy2(str(img_path), str(Path(output_img_dir) / img_name))

    count = len(list(Path(output_label_dir).glob("*.txt")))
    print(f"  [{quality}] 处理完成: {count} 张图像")


def create_dataset_yaml(output_dir, class_names):
    """生成 YOLO 训练用的 dataset.yaml"""
    yaml_path = Path(output_dir) / "dataset.yaml"
    abs_path = Path(output_dir).resolve()

    content = f"""# Weld Defect Detection Dataset (2-class)
path: {abs_path}
train: images/train
val: images/val

nc: {len(class_names)}
names: {class_names}
"""
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  dataset.yaml 已创建: {yaml_path}")


def main():
    """主函数: 转换整个数据集"""
    random.seed(42)

    base = Path(r"c:\Users\hyh\OneDrive\桌面\实训")
    data_dir = base / "data"
    output_dir = base / "datasets"

    # 收集所有图像
    all_images = []

    for quality in ["high", "low"]:
        img_dir = data_dir / quality / "images"
        if img_dir.exists():
            for img_file in img_dir.iterdir():
                if img_file.suffix.lower() in [".jpg", ".bmp", ".png"]:
                    all_images.append((quality, img_file.name))

    print(f"共找到 {len(all_images)} 张图像")

    # 随机打乱
    random.shuffle(all_images)

    # 80/20 划分
    split_idx = int(len(all_images) * 0.8)
    train_images = all_images[:split_idx]
    val_images = all_images[split_idx:]

    print(f"训练集: {len(train_images)} 张, 验证集: {len(val_images)} 张")

    # 按 quality 分组处理训练集
    train_high = [name for q, name in train_images if q == "high"]
    train_low = [name for q, name in train_images if q == "low"]
    val_high = [name for q, name in val_images if q == "high"]
    val_low = [name for q, name in val_images if q == "low"]

    print(f"\n--- 处理训练集 ---")
    print(f"high: {len(train_high)} 张, low: {len(train_low)} 张")

    process_split("high",
        data_dir / "high" / "images",
        data_dir / "high" / "defects",
        data_dir / "high" / "welds",
        output_dir / "images" / "train",
        output_dir / "labels" / "train",
        train_high,
    )
    process_split("low",
        data_dir / "low" / "images",
        data_dir / "low" / "defects",
        data_dir / "low" / "welds",
        output_dir / "images" / "train",
        output_dir / "labels" / "train",
        train_low,
    )

    print(f"\n--- 处理验证集 ---")
    print(f"high: {len(val_high)} 张, low: {len(val_low)} 张")

    process_split("high",
        data_dir / "high" / "images",
        data_dir / "high" / "defects",
        data_dir / "high" / "welds",
        output_dir / "images" / "val",
        output_dir / "labels" / "val",
        val_high,
    )
    process_split("low",
        data_dir / "low" / "images",
        data_dir / "low" / "defects",
        data_dir / "low" / "welds",
        output_dir / "images" / "val",
        output_dir / "labels" / "val",
        val_low,
    )

    # 生成 dataset.yaml
    create_dataset_yaml(output_dir, ["weld", "defect"])

    # 统计
    train_txts = list((output_dir / "labels" / "train").glob("*.txt"))
    val_txts = list((output_dir / "labels" / "val").glob("*.txt"))
    print(f"\n=== 转换完成 ===")
    print(f"训练集标注文件: {len(train_txts)}")
    print(f"验证集标注文件: {len(val_txts)}")


if __name__ == "__main__":
    main()
