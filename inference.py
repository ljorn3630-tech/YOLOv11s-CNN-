"""
推理流水线: YOLOv11s → ROI 裁剪 → EfficientNet-B0 精细分类
"""

import cv2
import numpy as np
from PIL import Image
import torch
from pathlib import Path
from ultralytics import YOLO
import timm

from weld_dataset import CNN_CLASSES, get_cnn_transforms


def imread_unicode(path):
    """读取图像，支持中文路径"""
    with open(path, "rb") as f:
        data = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    return img


# 缺陷类别的 BGR 颜色映射
DEFECT_COLORS = {
    "stain": (0, 255, 0),           # 绿色
    "discontinuity": (0, 0, 255),   # 红色
    "deposit": (255, 0, 0),         # 蓝色
    "pore": (0, 255, 255),          # 黄色
}

WELD_COLOR = (255, 255, 0)  # 青色


class WeldDefectDetector:
    """焊缝缺陷检测器：YOLO 粗检 + CNN 精分类"""

    def __init__(self, yolo_path, cnn_path, yolo_conf=0.25, yolo_iou=0.5,
                 cnn_conf=0.5, device=None):
        """
        Args:
            yolo_path: YOLOv11s 模型路径
            cnn_path: EfficientNet-B0 模型路径
            yolo_conf: YOLO 置信度阈值
            yolo_iou: YOLO NMS IoU 阈值
            cnn_conf: CNN 分类置信度阈值
            device: torch device
        """
        self.device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.yolo_conf = yolo_conf
        self.yolo_iou = yolo_iou
        self.cnn_conf = cnn_conf

        # 加载 YOLO 模型
        print(f"加载 YOLO 模型: {yolo_path}")
        self.yolo = YOLO(str(yolo_path))

        # 加载 CNN 模型
        print(f"加载 CNN 模型: {cnn_path}")
        checkpoint = torch.load(str(cnn_path), map_location=self.device, weights_only=True)
        self.num_classes = checkpoint["num_classes"]
        self.cnn_class_names = checkpoint["class_names"]

        self.cnn = timm.create_model("efficientnet_b0", pretrained=False, num_classes=self.num_classes)
        self.cnn.load_state_dict(checkpoint["model_state_dict"])
        self.cnn = self.cnn.to(self.device)
        self.cnn.eval()

        self.cnn_transform = get_cnn_transforms(is_train=False)

        print(f"模型加载完成. YOLO: 2类, CNN: {self.num_classes}类 [{', '.join(self.cnn_class_names)}]")

    def _classify_roi(self, roi_pil):
        """对单个 ROI 图像进行 CNN 分类"""
        tensor = self.cnn_transform(roi_pil).unsqueeze(0).to(self.device)

        with torch.no_grad():
            outputs = self.cnn(tensor)
            probs = torch.softmax(outputs, dim=1)
            conf, pred = probs.max(1)
            conf_val = conf.item()
            pred_class = pred.item()

        if conf_val >= self.cnn_conf:
            return self.cnn_class_names[pred_class], conf_val
        return None, 0.0

    def detect(self, image):
        """
        对输入图像执行完整推理流水线。

        Args:
            image: numpy.ndarray (H, W, 3) BGR 格式 或 PIL.Image

        Returns:
            dict: {
                "annotated_image": PIL.Image (RGB),
                "defects": [{"bbox": [x1,y1,x2,y2], "class": str, "confidence": float}, ...],
                "summary": {class_name: count, ...},
                "weld_areas": [{"bbox": [x1,y1,x2,y2], "confidence": float}, ...],
            }
        """
        # 输入处理
        if isinstance(image, Image.Image):
            image_np = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        else:
            image_np = image.copy()

        h, w = image_np.shape[:2]

        # Step 1: YOLO 推理
        yolo_results = self.yolo(image_np, conf=self.yolo_conf, iou=self.yolo_iou, verbose=False)

        defects = []
        weld_areas = []
        roi_images = []  # 待 CNN 分类的 ROI

        if yolo_results[0].boxes is not None:
            boxes = yolo_results[0].boxes

            for i in range(len(boxes)):
                cls_id = int(boxes.cls[i].item())
                conf = float(boxes.conf[i].item())
                xyxy = boxes.xyxy[i].cpu().numpy()  # [x1, y1, x2, y2]
                x1, y1, x2, y2 = map(int, xyxy)

                # 裁剪到图像范围
                x1, y1 = max(0, x1), max(0, y1)
                x2, y2 = min(w, x2), min(h, y2)

                if cls_id == 0:  # weld
                    weld_areas.append({"bbox": [x1, y1, x2, y2], "confidence": conf})

                elif cls_id == 1:  # defect → 送 CNN 精分类
                    if x2 > x1 and y2 > y1:
                        roi = image_np[y1:y2, x1:x2]
                        if roi.size > 0:
                            roi_rgb = cv2.cvtColor(roi, cv2.COLOR_BGR2RGB)
                            roi_pil = Image.fromarray(roi_rgb)
                            roi_images.append({
                                "roi": roi_pil,
                                "bbox": [x1, y1, x2, y2],
                                "yolo_conf": conf,
                            })

        # Step 2: CNN 逐框精细分类
        for roi_info in roi_images:
            cnn_class, cnn_conf = self._classify_roi(roi_info["roi"])

            if cnn_class is not None:
                defects.append({
                    "bbox": roi_info["bbox"],
                    "class": cnn_class,
                    "confidence": round(cnn_conf, 4),
                })
            else:
                # CNN 置信度不足，使用 YOLO 的 "defect" 通用标签
                defects.append({
                    "bbox": roi_info["bbox"],
                    "class": "defect",
                    "confidence": round(roi_info["yolo_conf"], 4),
                })

        # Step 3: 绘图
        annotated = cv2.cvtColor(image_np.copy(), cv2.COLOR_BGR2RGB)

        # 画焊缝区域
        for wa in weld_areas:
            x1, y1, x2, y2 = wa["bbox"]
            cv2.rectangle(annotated, (x1, y1), (x2, y2), WELD_COLOR, 2)
            label = f"weld {wa['confidence']:.2f}"
            self._draw_label(annotated, x1, y1, label, WELD_COLOR)

        # 画缺陷框
        for d in defects:
            x1, y1, x2, y2 = d["bbox"]
            color = DEFECT_COLORS.get(d["class"], (128, 128, 128))
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
            label = f"{d['class']} {d['confidence']:.2f}"
            self._draw_label(annotated, x1, y1, label, color)

        # Step 4: 统计
        summary = {}
        for d in defects:
            cls = d["class"]
            summary[cls] = summary.get(cls, 0) + 1

        return {
            "annotated_image": Image.fromarray(annotated),
            "defects": defects,
            "summary": summary,
            "weld_areas": weld_areas,
        }

    def _draw_label(self, image, x, y, text, color):
        """在图像上绘制标签背景+文字"""
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
        cv2.rectangle(image, (x, y - th - 4), (x + tw, y), color, -1)
        cv2.putText(image, text, (x, y - 2), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1, cv2.LINE_AA)

    def set_thresholds(self, yolo_conf=None, yolo_iou=None, cnn_conf=None):
        """动态调整阈值"""
        if yolo_conf is not None:
            self.yolo_conf = yolo_conf
        if yolo_iou is not None:
            self.yolo_iou = yolo_iou
        if cnn_conf is not None:
            self.cnn_conf = cnn_conf


def get_default_detector():
    """获取默认检测器实例"""
    base = Path(r"c:\Users\hyh\OneDrive\桌面\实训")
    yolo_path = base / "models" / "yolo11s_weld.pt"
    cnn_path = base / "models" / "efficientnet_defect.pth"

    return WeldDefectDetector(
        yolo_path=str(yolo_path),
        cnn_path=str(cnn_path),
    )


if __name__ == "__main__":
    # 测试推理
    import sys

    detector = get_default_detector()

    # 找一张测试图像
    base = Path(r"c:\Users\hyh\OneDrive\桌面\实训")
    test_images = list((base / "datasets" / "images" / "val").glob("*"))
    if test_images:
        test_img = imread_unicode(str(test_images[0]))
        print(f"测试图像: {test_images[0].name}, shape: {test_img.shape}")

        result = detector.detect(test_img)
        print(f"\n=== 检测结果 ===")
        print(f"焊缝区域: {len(result['weld_areas'])}")
        print(f"缺陷数量: {len(result['defects'])}")
        print(f"缺陷统计: {result['summary']}")
        for d in result['defects'][:5]:
            print(f"  - {d['class']}: conf={d['confidence']:.4f}, bbox={d['bbox']}")

        # 保存标注图
        out_path = base / "outputs" / "test_result.jpg"
        out_path.parent.mkdir(exist_ok=True)
        result["annotated_image"].save(str(out_path))
        print(f"\n标注图已保存: {out_path}")
    else:
        print("未找到测试图像")
