"""
EfficientNet-B0 4类缺陷分类器训练
"""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.optim.lr_scheduler import CosineAnnealingLR
from pathlib import Path
import numpy as np
from sklearn.metrics import classification_report, confusion_matrix
import timm

from weld_dataset import create_dataloaders, CNN_CLASSES


def train_epoch(model, loader, criterion, optimizer, device):
    model.train()
    running_loss = 0.0
    correct = 0
    total = 0

    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)

        optimizer.zero_grad()
        outputs = model(images)
        loss = criterion(outputs, labels)
        loss.backward()
        optimizer.step()

        running_loss += loss.item()
        _, predicted = outputs.max(1)
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()

    return running_loss / len(loader), 100.0 * correct / total


@torch.no_grad()
def validate(model, loader, criterion, device):
    model.eval()
    running_loss = 0.0
    correct = 0
    total = 0
    all_preds = []
    all_labels = []

    for images, labels in loader:
        images, labels = images.to(device), labels.to(device)

        outputs = model(images)
        loss = criterion(outputs, labels)

        running_loss += loss.item()
        _, predicted = outputs.max(1)
        total += labels.size(0)
        correct += predicted.eq(labels).sum().item()

        all_preds.extend(predicted.cpu().numpy())
        all_labels.extend(labels.cpu().numpy())

    acc = 100.0 * correct / total
    return running_loss / len(loader), acc, all_preds, all_labels


def main():
    base = Path(r"c:\Users\hyh\OneDrive\桌面\实训")
    cnn_data_dir = base / "datasets" / "cnn"
    models_dir = base / "models"
    models_dir.mkdir(exist_ok=True)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"设备: {device}")

    # 超参数
    num_epochs = 50
    batch_size = 32
    lr = 1e-4
    patience = 10
    num_classes = 4

    # 创建 DataLoader
    train_loader, val_loader, train_dataset, val_dataset = create_dataloaders(
        cnn_data_dir, batch_size=batch_size, num_workers=2
    )
    print(f"训练集: {len(train_dataset)} 样本, 验证集: {len(val_dataset)} 样本")

    # 创建模型
    model = timm.create_model("efficientnet_b0", pretrained=True, num_classes=num_classes)
    model = model.to(device)

    # 损失函数（带 Label Smoothing）
    criterion = nn.CrossEntropyLoss(label_smoothing=0.1)

    # 优化器
    optimizer = optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = CosineAnnealingLR(optimizer, T_max=num_epochs)

    # 训练循环
    best_acc = 0.0
    patience_counter = 0

    for epoch in range(num_epochs):
        train_loss, train_acc = train_epoch(model, train_loader, criterion, optimizer, device)
        val_loss, val_acc, val_preds, val_labels = validate(model, val_loader, criterion, device)

        scheduler.step()
        current_lr = scheduler.get_last_lr()[0]

        print(f"Epoch {epoch+1:3d}/{num_epochs} | "
              f"Train Loss: {train_loss:.4f}, Acc: {train_acc:.2f}% | "
              f"Val Loss: {val_loss:.4f}, Acc: {val_acc:.2f}% | "
              f"LR: {current_lr:.6f}")

        # Early stopping + 保存最佳模型
        if val_acc > best_acc:
            best_acc = val_acc
            patience_counter = 0
            torch.save({
                "epoch": epoch,
                "model_state_dict": model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_acc": val_acc,
                "num_classes": num_classes,
                "class_names": CNN_CLASSES,
            }, str(models_dir / "efficientnet_defect.pth"))
            print(f"  --> 最佳模型已保存 (acc={best_acc:.2f}%)")
        else:
            patience_counter += 1
            if patience_counter >= patience:
                print(f"\nEarly stopping at epoch {epoch+1}")
                break

    # 最终评估
    print(f"\n=== 训练完成 ===")
    print(f"最佳验证准确率: {best_acc:.2f}%")

    # 分类报告
    checkpoint = torch.load(str(models_dir / "efficientnet_defect.pth"), map_location=device, weights_only=True)
    model.load_state_dict(checkpoint["model_state_dict"])
    _, final_acc, final_preds, final_labels = validate(model, val_loader, criterion, device)

    print("\n分类报告:")
    print(classification_report(final_labels, final_preds, target_names=CNN_CLASSES))
    print("\n混淆矩阵:")
    print(confusion_matrix(final_labels, final_preds))


if __name__ == "__main__":
    main()
