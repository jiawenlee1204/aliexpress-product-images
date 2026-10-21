---
name: aliexpress-product-images
description: Use when 要按文件夹批量生成或重出速卖通商品主图（产品图+效果图 → 上架图）。
---

# 速卖通商品图批量出图

输入一批 `<SKU>产品图` + `<SKU>效果图`（可带 Excel 机位要求）→ 输出 `output/<SKU>.jpg`（1:1，2048²）。
接口是 **图生图** `/v1/images/edits`（产品图即图1 条件）；产品上的小字交给 `axn overlay` 叠印。

## 目录（项目根 = 用户当次给的文件夹）
```
output/              只有成品 <SKU>.jpg
_work/candidates/    候选 <SKU>_<候选>.jpg
_work/replaced/      被替换的旧成品
_work/specs.json     提示词规格（可手改）
```
来货结构：`<品类>图片方位需求/<SKU>/<SKU>产品图.* + <SKU>效果图.*` + `<品类>产品方位需求.xlsx`（A 列 SKU，B 列机位）。

## 环境
- `SENSENOVA_API_KEY` 只在交互 shell → 出图一律走 `terminal`（execute_code kernel 没有）。
- 唯一图像模型 `sensenova-u1.5-lite`；16~25s/张；串行 + 6s 错峰，并发直接 429。
- 脚本已装空 ProxyHandler 绕过 macOS 系统代理；路径全部 `--src` 传入，脚本内不写死。

```bash
AXN="${HERMES_HOME:-$HOME/.hermes}/skills/creative/aliexpress-product-images/axn"   # Hermes
# AXN="$HOME/.claude/skills/aliexpress-product-images/axn"                          # Claude Code
```

## 流程
1. `"$AXN" scan 新` —— 列 SKU + 读 Excel 机位要求（铁律，改机位前先回读）。
2. 用 vision 看图：**产品图 → identity**（部件/数量/颜色/材质/印字），**效果图 → 只取机位/光/背景/构图**。
   图2 里的产品、品牌字、价签一律禁止进成品。
3. 写六段 spec（`identity/camera/adapt/allow/forbid/tail`），套路见 `references/prompt-playbook.md`。
4. `"$AXN" render --specs _work/specs.json --src 新 --dry-run` 核对 → 去掉 `--dry-run` 真跑；每 SKU 出 A/B 两版（同装配、不同机位），整批一个 background job 跑完。
5. 逐张 vision 自查：① 数量一只只数 ② 装配是否真穿过、有无悬空 ③ 机位对不对 ④ 有无文字/水印 ⑤ 正反面/颜色/图案 ⑥ 同批版式是否雷同。
6. `"$AXN" pick --sku X --cand A` 定稿；`"$AXN" clean` 收尾。
7. 一键兜底：`"$AXN" auto --src 新 --review`（VLM 起草 → 出图 → 复核 → 定稿）。VLM 认产品不如人，出货前过一遍 `camera`。

## 六条硬规则（返工都出在这些上）
1. **数量写死**：「恰好 4 只：3 只正面朝上叠成一摞错开 8mm、1 只反面朝上单放」+ forbid 点名「第 5 只」。
2. **装配写「谁挡谁」**：先定轴与角度 → 贴合证据（长边平行/整条压住/无透光缝/螺丝沿轴一上一下）→ 遮挡（只在哪两处露出、不许从镂空穿出）。
3. **机位用否定句锚定**：「不是侧面机位」「严格俯视，不是平视不是斜拍」；只写「正面」模型会给侧面。
4. **穿戴图交代人与背景空间关系**：人坐在沙发前的地上、顶部带膝盖大腿、脚落地有投影、最近处略虚化，否则腿会悬浮。
5. **防判同款就换主体配色**：参考图那款颜色写进 forbid（如「橙色鞋」），指定新色（军绿/炭灰）。
6. **同批版式/底色必须拉开**：机位、背景材质与明度、道具与留白至少变一项，别整批落在同一个米色平铺里。

## 产品自带印字
模型把几十像素的小字当纹理画，必错。做法：先出「表面干净、禁止任何文字」的底图，再叠印

```bash
"$AXN" overlay --base 底图.jpg --out 成品.jpg --text "COMFORT|INSOLE"
```

自动识别主体 → PCA 拟合长轴 → 从后跟往脚尖排字 → multiply 叠印（`--ink 18` 保留纹理）。`|` 处插入空心圆角方形波浪边印章（真品的符号，不是皇冠）。
- 比例按真品量：`--len` = 整行长度/长轴、`--cap` = 字高/长轴（鞋垫实测 0.40 / 0.034）。带字面要正立，否则叠印会与底图透视错位。
- 改完用 `ImageChops.difference` 比上一版：均值应 <1、差异只在文字那条窄带；铺满半张图说明长轴拟合跑偏。
- 不要保留这行字时，把印字区裁出画面（废字比错字安全）。加放大参考图只改善符号形状，改不了拼写。

## 交付与分发
- **产物要干净**：只交付「一个入口 + 一个脚本 + 一份 README」。不为一行 `pip install` 单建 requirements.txt，不留两份能力重叠的脚本，不留只有一例的巨型样例——用户会明确要求「只保留最有必要的代码/产物/说明」，主动按这个标准收。
- **合并功能做成真子命令**：把第二个脚本并进来时，写成 `axn overlay ...` 这样的真 subparser，不要留 `subprocess` 转发（多一层进程、参数解析要维护两处，`--help` 还得靠包装器绕）。
- **合并/精简后证明行为没变**：用可复现比对，本 class 就是 `ImageChops.difference(新图, 上一版成品)`，均值 <1 且差异只落在文字带；跑通了再说「没改行为」。
- 报告只给 `output/` 路径 + 改了什么 + 候选在哪。项目里只放原料与成品：脚本都在本 skill，不在项目里留 `*.py`/备份/`__pycache__`。
- 分发形状固定：`axn` 单入口 + `install.sh`（自动装 `~/.claude/skills/` 与 `~/.hermes/skills/creative/`，`--target` 可指定）+ ≤100 行 README。
- 打包后必须验证：装到临时目录，用装出来的那份跑 `axn scan`，能列出 SKU 才算真能给别人用。
- **用户问「skill 在哪」**：直接给绝对路径（Hermes `${HERMES_HOME:-~/.hermes}/skills/creative/aliexpress-product-images/`，Claude Code `~/.claude/skills/...`），并说明它靠 description 自动触发、不需要手动调用。

## 已知坑
- **Pillow 只给 overlay 用**：模块顶部 `try: from PIL import ...; HAVE_PIL=True except ImportError: HAVE_PIL=False`，只在 `overlay` 里检查并报 `pip install Pillow`——否则没装 Pillow 的机器连 `scan`/`render` 都跑不起来。
- 中文串不许出现乱码字节（U+FFFD）：写完 `grep -c $'\xef\xbf\xbd'`；连示意用的 `��` 也别留在正文里，会原样进提示词。
- VLM 有时只返回 `reasoning` 没有 `content` → 取 `msg.get("content") or msg.get("reasoning")`；单次 2~3 分钟，timeout 300s + 重试 2 次，单 SKU 失败不要整批崩。
- 参考图里的鞋/靴/外搭一律进 forbid；袜子类在 tail 写死「长到膝盖下方、纵向细罗纹通体到底」。
