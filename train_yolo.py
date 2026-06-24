"""
YOLOv11s 训练脚本
2类检测: weld(0), defect(1)
"""

from ultralytics import YOLO
from pathlib import Path


def main():
    base = Path(r"c:\Users\hyh\OneDrive\桌面\实训")
    data_yaml = base / "datasets" / "dataset.yaml"
    models_dir = base / "models"
    models_dir.mkdir(exist_ok=True)

    print(f"数据集配置: {data_yaml}")
    print(f"模型输出目录: {models_dir}")

    # 加载预训练模型
    model = YOLO("yolo11s.pt")

    # 训练
    results = model.train(
        data=str(data_yaml),
        epochs=100,
        patience=15,
        imgsz=640,
        batch=16,
        device=0,  # GPU
        optimizer="AdamW",
        lr0=0.001,
        lrf=0.01,
        cos_lr=True,
        warmup_epochs=3,
        augment=True,
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.4,
        degrees=10.0,
        translate=0.1,
        scale=0.5,
        fliplr=0.5,
        mosaic=1.0,
        project=str(base / "outputs"),
        name="yolo_train",
        exist_ok=True,
        save=True,
        save_period=10,
        val=True,
        plots=True,
    )

    # 复制最佳模型到 models 目录
    best_pt = Path(results.save_dir) / "weights" / "best.pt"
    if best_pt.exists():
        import shutil
        shutil.copy2(str(best_pt), str(models_dir / "yolo11s_weld.pt"))
        print(f"\n最佳模型已保存: {models_dir / 'yolo11s_weld.pt'}")

    # 打印评估结果
    print(f"\n=== 训练完成 ===")
    print(f"mAP@0.5: {results.results_dict.get('metrics/mAP50(B)', 'N/A')}")
    print(f"mAP@0.5:0.95: {results.results_dict.get('metrics/mAP50-95(B)', 'N/A')}")


if __name__ == "__main__":
    main()
