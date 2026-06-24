"""
工业焊缝缺陷检测系统 — Gradio Web 界面
Weld Defect Inspection System
"""

import gradio as gr
import json
import time
from pathlib import Path
from PIL import Image
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from io import BytesIO

from inference import get_default_detector

# ── 全局 ──────────────────────────────────────────────
detector = None

DEFECT_HEX = {
    "stain": "#22C55E", "discontinuity": "#EF4444",
    "deposit": "#3B82F6", "pore": "#F59E0B", "defect": "#6B7280",
}

DEFECT_CN = {
    "stain": "油污 / 斑渍", "discontinuity": "裂纹 / 不连续",
    "deposit": "沉积物", "pore": "气孔", "defect": "疑似缺陷",
}

SEVERITY = {
    "discontinuity": "⚠ 高危", "pore": "⚠ 中危",
    "deposit": "● 中危", "stain": "● 低危", "defect": "—",
}


def load_detector():
    global detector
    if detector is None:
        detector = get_default_detector()
    return detector


# ── 统计图表 ──────────────────────────────────────────
def make_stats_chart(summary, weld_count, defect_count):
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 2.6), facecolor="#0f172a")

    # 左：条形图
    if summary:
        labels, values, colors = [], [], []
        for cls in ["stain", "discontinuity", "deposit", "pore", "defect"]:
            if cls in summary:
                labels.append(DEFECT_CN.get(cls, cls))
                values.append(summary[cls])
                colors.append(DEFECT_HEX[cls])
        bars = ax1.barh(labels[::-1], values[::-1], color=colors[::-1],
                        height=0.55, edgecolor="none")
        for bar, val in zip(bars, values[::-1]):
            ax1.text(bar.get_width() + max(values) * 0.03, bar.get_y() + bar.get_height() / 2,
                     str(val), va="center", fontsize=10, color="#ffffff", fontweight="600")
    else:
        ax1.text(0.5, 0.5, "未检测到缺陷", transform=ax1.transAxes, ha="center",
                 va="center", fontsize=13, color="#64748b")
    ax1.set_title("缺陷类别分布", fontsize=12, color="#ffffff", fontweight="600", pad=6)
    ax1.set_facecolor("#0f172a")
    ax1.tick_params(colors="#94a3b8", labelsize=9)
    for spine in ax1.spines.values():
        spine.set_visible(False)
    ax1.xaxis.set_visible(False)

    # 右：判定
    ax2.set_xlim(0, 10); ax2.set_ylim(0, 10); ax2.axis("off")
    ax2.set_facecolor("#0f172a")
    has_critical = "discontinuity" in summary or "pore" in summary
    if defect_count == 0:
        status_text, status_color = "✓ 合格", "#22C55E"
    elif has_critical:
        status_text, status_color = "⚠ 不合格", "#EF4444"
    else:
        status_text, status_color = "● 需复核", "#F59E0B"

    ax2.text(5, 9.0, "检测判定", ha="center", fontsize=12, color="#ffffff", fontweight="600")
    ax2.text(5, 6.5, status_text, ha="center", fontsize=32, color=status_color, fontweight="bold")
    ax2.text(5, 3.5, f"焊缝 {weld_count} 处  |  缺陷 {defect_count} 处",
             ha="center", fontsize=10, color="#94a3b8")

    fig.tight_layout(pad=2)
    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=130, bbox_inches="tight", facecolor="#0f172a", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    return Image.open(buf)


# ── 核心回调 ──────────────────────────────────────────
def detect_image(image, yolo_conf, yolo_iou, cnn_conf):
    if image is None:
        return (None, None, "### 请上传焊缝图像以开始检测", gr.update(visible=False), None, None)

    t0 = time.time()
    det = load_detector()
    det.set_thresholds(float(yolo_conf), float(yolo_iou), float(cnn_conf))
    result = det.detect(image)
    elapsed = (time.time() - t0) * 1000

    annotated = result["annotated_image"]
    defects = result["defects"]
    summary = result["summary"]
    weld_count = len(result["weld_areas"])
    defect_count = len(defects)

    chart = make_stats_chart(summary, weld_count, defect_count)

    # 状态
    has_critical = "discontinuity" in summary or "pore" in summary
    if defect_count == 0:
        status = f"### ✅ 未检测到缺陷 | 焊缝区域 {weld_count} 处 | 推理 {elapsed:.0f} ms"
    elif has_critical:
        status = f"### ⚠ 检出 {defect_count} 处缺陷 (含高危裂纹/气孔) | 建议人工复核 | {elapsed:.0f} ms"
    else:
        status = f"### ● 检出 {defect_count} 处缺陷 | 焊缝区域 {weld_count} 处 | {elapsed:.0f} ms"

    # 表格
    table_data = []
    for i, d in enumerate(defects, 1):
        x1, y1, x2, y2 = d["bbox"]
        cls_name = d["class"]
        conf = d["confidence"]
        conf_bar = "█" * min(int(conf * 10), 10) + "░" * max(0, 10 - int(conf * 10))
        sev = SEVERITY.get(cls_name, "—")
        table_data.append([
            str(i),
            DEFECT_CN.get(cls_name, cls_name),
            sev,
            f"{conf:.2%}  {conf_bar}",
            f"({x1}, {y1}) → ({x2}, {y2})",
            f"{x2-x1}×{y2-y1}",
        ])
    if not table_data:
        table_data = [["—", "未检测到缺陷", "—", "—", "—", "—"]]

    # 报告
    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_weld_areas": weld_count,
        "total_defects": defect_count,
        "summary": summary,
        "inference_ms": round(elapsed, 1),
        "thresholds": {"yolo_conf": yolo_conf, "yolo_iou": yolo_iou, "cnn_conf": cnn_conf},
        "defects": [
            {"index": i, "class": d["class"], "class_cn": DEFECT_CN.get(d["class"], d["class"]),
             "confidence": d["confidence"], "severity": SEVERITY.get(d["class"], "—"),
             "bbox": d["bbox"],
             "size": f"{d['bbox'][2]-d['bbox'][0]}×{d['bbox'][3]-d['bbox'][1]}"}
            for i, d in enumerate(defects, 1)
        ],
    }
    report_dir = Path(r"c:\Users\hyh\OneDrive\桌面\实训\outputs")
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"report_{time.strftime('%Y%m%d_%H%M%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    return (annotated, chart, status, gr.update(value=defect_count, visible=True), table_data, str(report_path))


# ── UI ────────────────────────────────────────────────
CSS = """
/* ══ 强行穿透并横向撑满全屏宽度 ══ */
div.gradio-container, .gradio-container-5-0-0, [data-testid="grid"], .gradio-container main {
    max-width: 98% !important;
    width: 98% !important;
    margin: 0 auto !important;
    background: #0b1120 !important;
}

body, html {
    background-color: #0b1120 !important;
}

/* ══ 顶部标题栏 ══ */
.header-bar {
    background: linear-gradient(135deg, #1e3b5c 0%, #0f1d30 100%);
    border-bottom: 2px solid #2563eb55;
    padding: 22px 32px;
    margin: -16px -16px 20px -16px;
    border-radius: 0 0 8px 8px;
}
.header-bar h1 {
    margin: 0 0 4px 0;
    font-size: 1.7em;
    font-weight: 800;
    color: #ffffff !important;
    letter-spacing: 0.02em;
    text-align: center;
}
.header-bar .sub {
    font-size: 0.88em;
    color: #93c5fd;
    margin-top: 2px;
    text-align: center;
}

/* ══ 卡片样式 ══ */
.section-label {
    font-size: 0.9em;
    font-weight: 700;
    color: #94a3b8;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    margin-bottom: 10px;
}

/* ══ 输入框虚线边框 ══ */
#input-col {
    border: 2px dashed #1e3b5c;
    border-radius: 14px;
    padding: 16px;
    background: #0d1520;
}
#input-col:hover { border-color: #3b82f6; }

/* ══ 按钮 ══ */
button.primary {
    background: linear-gradient(135deg, #2563eb, #1d4ed8) !important;
    border: none !important;
    color: #ffffff !important;
    font-weight: 700 !important;
    border-radius: 8px !important;
    padding: 10px 0 !important;
    font-size: 0.95em !important;
    letter-spacing: 0.03em !important;
    box-shadow: 0 2px 10px #2563eb55 !important;
    transition: all 0.2s !important;
}
button.primary:hover {
    background: linear-gradient(135deg, #3b82f6, #2563eb) !important;
    box-shadow: 0 4px 18px #2563eb77 !important;
    transform: translateY(-1px) !important;
}

/* ══ 表格 ══ */
table { border-collapse: collapse !important; width: 100% !important; }
th {
    background: #1e293b !important; color: #94a3b8 !important;
    font-weight: 700 !important; font-size: 0.82em !important;
    text-transform: uppercase; letter-spacing: 0.05em;
    padding: 12px 10px !important; border-bottom: 2px solid #334155 !important;
}
td {
    padding: 10px 10px !important; border-bottom: 1px solid #1e293b !important;
    font-size: 0.88em; color: #cbd5e1 !important;
}

/* ══ 滑块 ══ */
input[type=range] { accent-color: #3b82f6; }

/* ══ 底部 ══ */
.footer-bar {
    text-align: center; color: #475569; font-size: 0.8em;
    margin-top: 30px; padding-top: 20px; border-top: 1px solid #1e293b;
}

footer { display: none !important; }
"""


def create_ui():
    with gr.Blocks(title="工业焊缝缺陷检测系统") as app:
        gr.HTML(f"<style>{CSS}</style>")

        # ── 顶部 ──
        gr.HTML("""
        <div class="header-bar">
            <h1>工业焊缝缺陷检测系统</h1>
            <div class="sub">Weld Defect Inspection — YOLOv11s 检测 + EfficientNet-B0 精细分类</div>
        </div>
        """)

        # ── 上半部分：图像并排 ──
        with gr.Row(equal_height=True):
            with gr.Column(scale=1, elem_id="input-col"):
                gr.HTML('<div class="section-label">📤 输入图像</div>')
                input_image = gr.Image(
                    type="pil", sources=["upload", "clipboard", "webcam"],
                    label=None, show_label=False,
                )

            with gr.Column(scale=1):
                gr.HTML('<div class="section-label">🖼 检测标注结果</div>')
                output_image = gr.Image(type="pil", label=None, show_label=False)

        # ── 参数栏 ──
        with gr.Row():
            with gr.Column(scale=1):
                yolo_conf = gr.Slider(0.10, 0.90, value=0.25, step=0.05,
                                      label="YOLO 检测阈值")
            with gr.Column(scale=1):
                yolo_iou = gr.Slider(0.10, 0.90, value=0.50, step=0.05,
                                     label="NMS 重叠阈值")
            with gr.Column(scale=1):
                cnn_conf = gr.Slider(0.10, 0.90, value=0.50, step=0.05,
                                     label="CNN 分类阈值")
            with gr.Column(scale=1):
                gr.Markdown("")  # spacer
                with gr.Row():
                    reset_btn = gr.Button("↺ 默认", variant="secondary", size="sm")
                    detect_btn = gr.Button("🔍 开始检测", variant="primary", size="sm", elem_classes=["primary"])

        # ── 状态 ──
        status_text = gr.Markdown("### 等待图像输入...")

        # ── 下半部分：统计图 + 表格并排 ──
        with gr.Row():
            with gr.Column(scale=1):
                gr.HTML('<div class="section-label">📊 检测报告与统计</div>')
                stats_chart = gr.Image(type="pil", label=None, show_label=False)
                with gr.Row():
                    defect_count = gr.Number(value=0, label="检出缺陷总数", visible=False, precision=0)
                    download_btn = gr.DownloadButton("📥 下载 JSON 报告", variant="secondary")

            with gr.Column(scale=1):
                gr.HTML('<div class="section-label">📋 缺陷详情列表</div>')
                defects_table = gr.Dataframe(
                    headers=["#", "缺陷类型", "等级", "置信度", "坐标 (x1,y1 → x2,y2)", "尺寸"],
                    label=None, interactive=False, wrap=True,
                )

        # ── 底部 ──
        gr.HTML("""
        <div class="footer-bar">
            Weld Defect Inspection System v1.0 &nbsp;|&nbsp;
            拖拽上传 · 文件选择 · 摄像头实时采集 &nbsp;|&nbsp;
            支持 JPG / BMP / PNG &nbsp;|&nbsp;
            缺陷类别：油污 · 裂纹 · 沉积物 · 气孔
        </div>
        """)

        # ── 事件 ──
        ins = [input_image, yolo_conf, yolo_iou, cnn_conf]
        outs = [output_image, stats_chart, status_text, defect_count, defects_table, download_btn]

        detect_btn.click(fn=detect_image, inputs=ins, outputs=outs)
        input_image.change(fn=detect_image, inputs=ins, outputs=outs)
        reset_btn.click(fn=lambda: (0.25, 0.50, 0.50), outputs=[yolo_conf, yolo_iou, cnn_conf])

    return app


if __name__ == "__main__":
    print("╔══════════════════════════════════════════════════╗")
    print("║      🔧 工业焊缝缺陷检测系统 v1.0                ║")
    print("║   YOLOv11s (2类检测) + EfficientNet-B0 (4分类)   ║")
    print("╚══════════════════════════════════════════════════╝")
    print("\n正在加载 AI 模型...")
    load_detector()
    print("模型就绪. 启动 Web 服务...\n")

    app = create_ui()
    
    # 💡 核心修改：theme 参数使用内置暗黑主题对象，彻底清洗底层的白底边框缺陷
    app.launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
    )