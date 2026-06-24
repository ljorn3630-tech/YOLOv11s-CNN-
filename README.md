# 🔧 工业焊缝缺陷检测系统

基于 **YOLOv11s + EfficientNet-B0** 两级级联的焊缝缺陷自动检测系统，支持焊缝区域定位、缺陷候选框生成与四种缺陷类型的精细分类，提供 Gradio Web 可视化交互界面。

---

## 目录

- [项目结构](#项目结构)
- [环境配置](#环境配置)
- [数据集](#数据集)
- [技术架构](#技术架构)
- [模型训练](#模型训练)
- [推理流水线](#推理流水线)
- [Web 界面](#web-界面)
- [快速开始](#快速开始)

---

## 项目结构

```
main/
├── data/                              # 原始数据集
│   ├── high/                          # 高质量子集 (1022张 JPG, 2048×1080)
│   │   ├── images/                    # 焊缝图像
│   │   ├── defects/                   # 缺陷标注 (LabelMe JSON)
│   │   └── welds/                     # 焊缝区域标注 (LabelMe JSON)
│   └── low/                           # 低质量子集 (1000张 BMP, 640×480)
│       ├── images/
│       ├── defects/
│       └── welds/
│
├── datasets/                          # 转换后的训练数据集
│   ├── images/train/                  # 训练图像 (1617张)
│   ├── images/val/                    # 验证图像 (405张)
│   ├── labels/train/                  # YOLO标注 (2类: 0=weld, 1=defect)
│   ├── labels/val/
│   ├── dataset.yaml                   # YOLO训练配置
│   └── cnn/                           # CNN分类数据集 (ROI裁剪)
│       ├── train/                     # 13829张 (按类别分子文件夹)
│       └── val/                       # 3435张
│
├── models/                            # 训练好的模型权重
│   ├── yolo11s_weld.pt                # YOLOv11s 2类检测模型 (73MB)
│   └── efficientnet_defect.pth        # EfficientNet-B0 4类分类模型 (47MB)
│
├── src/                               # 源代码
│   ├── convert_dataset.py             # 数据集格式转换 (LabelMe→YOLO)
│   ├── train_yolo.py                  # YOLOv11s 训练脚本
│   ├── train_cnn.py                   # EfficientNet-B0 训练脚本
│   ├── weld_dataset.py                # CNN数据集构建 + PyTorch Dataset类
│   ├── inference.py                   # 推理流水线 (YOLO+CNN级联)
│   ├── app.py                         # Gradio Web界面
│
├── outputs/                           # 推理结果输出
├── requirements.txt                   # Python依赖
└── README.md                          # 本文件
```

---

### 安装依赖

```bash
F:\anaconda\envs\torch_gpu\python.exe -m pip install -r requirements.txt
```

`requirements.txt` 内容：

```
torch>=2.0.0
torchvision>=0.15.0
ultralytics>=8.3.0
opencv-python>=4.8.0
gradio>=4.0.0
Pillow>=10.0.0
numpy>=1.24.0
timm>=0.9.0
scikit-learn>=1.3.0
matplotlib>=3.7.0
```

---

## 数据集

### 来源

工业焊缝数据集共 **2022 张**图像，按质量分为两级：

| 子集 | 数量 | 格式 | 分辨率 |
|------|------|------|--------|
| high (高质量) | 1022 | JPG | 2048×1080 |
| low (低质量) | 1000 | BMP | 640×480 |

### 原始标注 (LabelMe JSON)

每张图像包含两组矩形框标注：

- **welds/*.json** — 焊缝区域，标签：`weld`、`part_weld`
- **defects/*.json** — 缺陷区域，标签：`stain`、`discontinuity`、`deposit`、`pore`

### 标注格式转换

执行 `src/convert_dataset.py` 完成以下转换：

**1. LabelMe → YOLO 归一化格式**

```
LabelMe: [[x1,y1], [x2,y2]]              (绝对坐标)
         ↓
YOLO:    class_id center_x center_y w h  (归一化至 [0,1])
```

转换公式：

```
center_x = (min(x1,x2) + max(x1,x2)) / 2 / image_width
center_y = (min(y1,y2) + max(y1,y2)) / 2 / image_height
width    = |x1 - x2| / image_width
height   = |y1 - y2| / image_height
```

**2. 类别映射（6类 → 2类）**

```
原始6类                              YOLO 2类         CNN 4类
┌─────────────────┐              ┌──────────────┐   ┌─────────────────┐
weld          ──┐                               │              │   │                 │
part_weld     ──┼──▶  class 0: weld             │              │   │                 │
                │                               │   YOLOv11s   │   │                 │
stain          ──┤                               │   检测模型    │   │                 │
discontinuity  ──┼──▶  class 1: defect ─────────▶│              │──▶│ EfficientNet-B0 │
deposit        ──┤                               │              │   │   分类模型       │
pore           ──┘                               └──────────────┘   │ stain (油污)    │
              └─────────────────┘                                   │ disc. (裂纹)    │
                                                                    │ deposit (沉积)  │
                                                                    │ pore (气孔)     │
                                                                    └─────────────────┘
```

**3. 数据集划分**

- 训练集：**1617 张** (80%)
- 验证集：**405 张** (20%)
- high 和 low 混合后随机打乱，保证均匀分布

### 缺陷类别分布

| 类别 | 英文 | High | Low | 合计 | 占比 |
|------|------|------|-----|------|------|
| 油污/斑渍 | stain | 4181 | 4126 | 8307 | 37.0% |
| 裂纹/不连续 | discontinuity | 2977 | 4243 | 7220 | 32.1% |
| 沉积物 | deposit | 1794 | 1141 | 2935 | 13.1% |
| 气孔 | pore | 523 | 3427 | 3950 | 17.6% |

### CNN 分类数据集

基于 GT 标注框从原始图像裁剪 ROI 并缩放至 224×224：

| 数据集 | 样本数 |
|--------|--------|
| 训练集 | 13829 |
| 验证集 | 3435 |
| 合计 | 17264 |

---

## 技术架构

### 总体设计

系统采用 **"粗检测 + 精分类"** 两级级联解耦架构：

```
输入图像
    │
    ▼
┌─────────────────────────────────────┐
│  YOLOv11s  (第一级)                  │
│  输入: 640×640                       │
│  任务: 2类目标检测                    │
│  输出: weld 焊缝区域 + defect 缺陷候选 │
│  模型大小: 9.4M 参数, 18.4MB          │
└────────┬────────────────────────────┘
         │
    ┌────┴────┐
    ▼         ▼
  weld框   defect候选框
  (直接     │
  输出)     ▼
         ┌─────────────────────────────┐
         │  NMS 过滤 (IoU=0.5)          │
         │  ROI 裁剪 → 224×224          │
         └────────┬────────────────────┘
                  │
                  ▼
         ┌─────────────────────────────┐
         │  EfficientNet-B0  (第二级)   │
         │  输入: 224×224                │
         │  任务: 4类精细分类             │
         │  输出: stain / discontinuity  │
         │        / deposit / pore       │
         │  模型大小: 5.3M 参数, 47MB     │
         └────────┬────────────────────┘
                  │
                  ▼
         ┌─────────────────────────────┐
         │  结果合并                      │
         │  YOLO定位 + CNN分类            │
         │  → 标注叠加图 + 统计 + 报告     │
         └─────────────────────────────┘
```

### 设计理由

- **YOLO 专注定位**：2 类比直接检测 6 类收敛更快，mAP@0.5 从 0.793 提升至 0.872
- **CNN 专注分类**：在 224×224 高分辨率 ROI 上提取纹理/形状特征，裂纹分类准确率提升约12个百分点
- **GT 数据集独立构建**：CNN 数据集基于 GT 标注框裁剪，与 YOLO 训练并行执行，避免误差级联传播

---

## 模型训练

### YOLOv11s 检测模型

| 配置项 | 值 |
|--------|-----|
| 预训练权重 | `yolo11s.pt` (COCO) |
| 输入尺寸 | 640×640 |
| 类别数 | 2 (weld, defect) |
| Epochs | 100 (early stopping patience=15) |
| Batch size | 16 |
| 优化器 | AdamW, lr=0.001, cosine scheduler |
| Warmup | 3 epochs |
| 数据增强 | Mosaic, HSV随机, 翻转, 旋转, 缩放 |
| 训练集/验证集 | 1617 / 405 |

```bash
F:\anaconda\envs\torch_gpu\python.exe src/train_yolo.py
```

### EfficientNet-B0 分类模型

| 配置项 | 值 |
|--------|-----|
| 预训练权重 | `efficientnet_b0` (ImageNet) |
| 输入尺寸 | 224×224 |
| 类别数 | 4 (stain, discontinuity, deposit, pore) |
| Epochs | 50 (early stopping patience=10) |
| Batch size | 32 |
| 优化器 | Adam, lr=0.0001, cosine scheduler |
| 损失函数 | CrossEntropy + Label Smoothing (ε=0.1) |
| 类别均衡 | WeightedRandomSampler |
| 数据增强 | RandAugment, Mixup |
| 训练集/验证集 | 13829 / 3435 |

```bash
F:\anaconda\envs\torch_gpu\python.exe src/train_cnn.py
```

### 实验结果

| 指标 | 数值 |
|------|------|
| YOLOv11s mAP@0.5 (2类) | 0.872 |
| YOLOv11s mAP@0.5:0.95 | 0.615 |
| EfficientNet-B0 分类准确率 | 91.8% |
| 端到端推理时延 (GPU) | < 1 秒/张 |
| pore (气孔) 召回率 | 0.84 |
| discontinuity (裂纹) F1 | 0.893 |

---

## 推理流水线

`src/inference.py` 中的 `WeldDefectDetector` 类实现了完整的 6 步级联推理：

```
Step 1: 图像输入 → 统一为 BGR numpy 数组
Step 2: YOLOv11s 前向推理 (640×640, GPU)
Step 3: NMS 去重过滤 (IoU=0.5, conf>0.25)
Step 4: 分离 weld/defect → defect框逐框 ROI 裁剪 → 224×224
Step 5: EfficientNet-B0 逐框 4 类分类
Step 6: OpenCV 绘制标注叠加图 + 合并结果
```

### 合并策略

- weld 框 → YOLO 直接输出
- defect 框 → YOLO 提供坐标，CNN 提供精细类别
- CNN 置信度低于阈值 → 标记为通用 `defect` 标签

### 输出结构

```python
{
    "annotated_image": PIL.Image,        # 标注叠加图 (RGB)
    "defects": [                          # 缺陷列表
        {"bbox": [x1,y1,x2,y2], "class": "pore", "confidence": 0.92},
        ...
    ],
    "summary": {                          # 缺陷统计
        "stain": 3, "discontinuity": 1,
        "deposit": 0, "pore": 2
    },
    "weld_areas": [                       # 焊缝区域
        {"bbox": [x1,y1,x2,y2], "confidence": 0.98}
    ]
}
```

```bash
# 命令行测试推理
F:\anaconda\envs\torch_gpu\python.exe src/inference.py
```

---

## Web 界面

基于 **Gradio 6.14.0** 开发的工业焊缝缺陷检测可视化界面。

### 启动

```bash
F:\anaconda\envs\torch_gpu\python.exe src/app.py
```

服务地址：`http://127.0.0.1:7860`

### 功能清单

| 功能 | 说明 |
|------|------|
| 拖拽上传 | 将图像拖入上传区自动检测 |
| 文件上传 | 点击选择本地 JPG/BMP/PNG 图像 |
| 摄像头拍摄 | 调用摄像头实时采集并检测 |
| 标注叠加图 | 焊缝区域(青色框) + 缺陷(按颜色区分: 绿/红/蓝/黄) |
| 检测判定 | 自动判定：合格 / 需复核 / 不合格 |
| 统计图表 | 缺陷类别分布水平条形图 + 判定卡片 |
| 缺陷详情表 | 序号、类型(中文)、严重等级、置信度(含可视化条)、坐标、尺寸 |
| 参数调节 | YOLO阈值 / NMS IoU / CNN分类阈值 (滑块实时生效) |
| JSON报告 | 下载含完整检测信息的结构化报告 |

### 界面设计

- 暗色工业主题 (#0b1120 深蓝黑背景)
- 蓝色调标题栏 + 技术栈徽章
- 虚线边框输入区 (hover 高亮)
- 蓝紫渐变按钮 (hover 动效)
- 缺陷框颜色编码：油污-绿、裂纹-红、沉积物-蓝、气孔-黄

### 缺陷中文映射

| 英文 | 中文 | 严重等级 |
|------|------|----------|
| stain | 油污/斑渍 | ● 低危 |
| discontinuity | 裂纹/不连续 | ⚠ 高危 |
| deposit | 沉积物 | ● 中危 |
| pore | 气孔 | ⚠ 中危 |





## 快速开始

### 1. 环境准备

```bash
# 确保使用正确的 Python 环境
F:\anaconda\envs\torch_gpu\python.exe -m pip install -r requirements.txt
```

### 2. 数据转换

```bash
F:\anaconda\envs\torch_gpu\python.exe src/convert_dataset.py
```

### 3. 训练模型

```bash
# YOLOv11s (可并行执行)
F:\anaconda\envs\torch_gpu\python.exe src/train_yolo.py

# CNN数据集构建 (可并行执行)
F:\anaconda\envs\torch_gpu\python.exe src/weld_dataset.py

# EfficientNet-B0 (需要CNN数据集)
F:\anaconda\envs\torch_gpu\python.exe src/train_cnn.py
```

### 4. 测试推理

```bash
F:\anaconda\envs\torch_gpu\python.exe src/inference.py
```

### 5. 启动 Web 界面

```bash
F:\anaconda\envs\torch_gpu\python.exe src/app.py
```

浏览器访问 `http://127.0.0.1:7860`

---

## 注意事项

1. **中文路径**：OpenCV `cv2.imread()` 不支持中文路径，本项目使用 `np.fromfile()` + `cv2.imdecode()` 方案解决
2. **GPU 显存**：YOLOv11s 训练 batch=16 约需 4.5GB，EfficientNet-B0 训练 batch=32 约需 2GB，6GB VRAM 足够
3. **类别不均衡**：CNN 训练使用 WeightedRandomSampler 进行均衡采样，Label Smoothing 缓解过拟合
4. **数据集划分**：随机种子固定为 42，确保实验结果可复现
<img width="567" height="286" alt="图片" src="https://github.com/user-attachments/assets/faf940f0-f6e4-48b0-bf3c-34b74fd0b9e3" />
<img width="570" height="230" alt="图片" src="https://github.com/user-attachments/assets/1e541935-ddf7-4cd9-bd59-a3231892bc48" />

