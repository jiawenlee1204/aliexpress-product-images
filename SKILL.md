---
name: aliexpress-product-images
description: 从一批产品图与效果图批量生成速卖通商品主图（图生图），换机位、出上架图时使用。
---

# 速卖通商品图批量出图

每个 SKU 两张素材：`<SKU>产品图` 是产品本体的唯一依据，`<SKU>效果图` 只用来参考机位、光线、背景和构图。
成品是 1:1、2048×2048 的 JPEG，落在 `output/<SKU>.jpg`。

生成走图生图接口 `POST {api_base}/images/edits`，一次请求里同时带产品图和效果图。
效果图里画的产品、品牌字、价签都不能进成品。产品身上的印刷小字模型画不准，交给 `overlay` 在干净底图上叠印。

## 配置

端点和模型由用户提供。必须是支持图生图或图像编辑的模型——接收 `images` 输入、按提示词改机位；纯文生图模型用不了。

```bash
export AXN_API_BASE=https://token.sensenova.cn/v1
export AXN_API_KEY=sk-...
export AXN_IMAGE_MODEL=sensenova-u1.5-lite      # 换成用户的图生图模型
export AXN_VLM_MODEL=sensenova-6.8-flash-lite   # 可选，只有 spec/auto 起草提示词时需要
```

项目根也可以放 `.axn.json` 覆盖：`{"api_base": "...", "image_model": "...", "vlm_model": "...", "size": "2048x2048", "api_key_env": "MY_KEY"}`。
密钥只从环境变量读，不落文件。没配 `AXN_VLM_MODEL` 时 `spec`/`auto` 会直接提示，不走模型。

## 项目目录

```
output/            成品 <SKU>.jpg（只有成品）
_work/candidates/  候选 <SKU>_<候选>.jpg
_work/replaced/    被替换的旧成品
_work/specs.json   提示词规格，可手改
```

来货目录是 `<品类>图片方位需求/<SKU>/<SKU>产品图.*` 加 `<SKU>效果图.*`，旁边可能有 `<品类>产品方位需求.xlsx`。

## 流程

```bash
python3 scripts/axn.py scan 新
```

列出 SKU 和 Excel 里每个 SKU 的机位要求（A 列 SKU，B 列一句话）。这一列是硬要求，改机位之前先回读。

接着看两张图写 `_work/specs.json`：产品图决定 identity（部件、数量、颜色、材质、印字），效果图决定 camera 和 adapt。
写法见 `references/prompt-playbook.md`。

```bash
python3 scripts/axn.py render --specs _work/specs.json --src 新 --dry-run
python3 scripts/axn.py render --specs _work/specs.json --src 新
```

每个 SKU 至少 A、B 两版：同一套装配关系，不同机位强调。整批放一个后台任务跑完，别一张一停；接口单并发，串行加 6s 错峰。

出图后逐张自查：数量一只只数，装配是否真的穿过、有没有悬空或穿插，机位是不是 Excel 要的，有没有多余文字水印，正反面和颜色是否和产品图一致，同一批几张摆在一起会不会像一套。

```bash
python3 scripts/axn.py pick --sku SK0020603 --cand A
python3 scripts/axn.py clean
```

赶时间可以用 `python3 scripts/axn.py auto --src 新 --review` 一条命令跑完。但起草提示词的是看图模型，产品识别和机位判断不如人，出货前仍应手改 camera。

## 提示词里最要紧的几件事

数量写死并给出数法，比如「恰好 4 只：3 只正面朝上叠成一摞、错开 8mm，1 只反面朝上单放」，再在 forbid 里点名第 5 只。

装配关系不要写几何，写谁挡谁。先定轴向和角度，再给贴合证据（长边平行、整条压住、没有透光缝），最后写遮挡（只在哪两处露出、不许从镂空穿出）。

机位用否定句锚定。「不是侧面机位」「严格俯视，不是平视不是斜拍」——只写「正面」，模型会给侧面。

穿戴类先交代人与背景的空间关系：人坐在沙发前的地上、画面顶部带膝盖和大腿、脚落地有投影、最近处略虚化。少了这几句，腿就是悬空的。

另外两条来自返工：要避免和参考图那款撞成同一个商品，就把原配色写进 forbid 并指定新色；
同一批的机位、背景材质和明度要拉开，别整批落在同一个米色平铺里。

## 产品印字

先让模型出一张「表面干净、禁止任何文字」的底图，再叠印：

```bash
python3 scripts/axn.py overlay --base 底图.jpg --out 成品.jpg --text "COMFORT|INSOLE"
```

脚本找主体像素、PCA 拟合长轴，沿长轴从后跟侧排字，再 multiply 上去保留底材纹理。
`|` 之间留空是插入符号：`--text "COMFORT| |INSOLE"`，默认画空心圆角方形波浪边印章。

比例按真品量：`--len` 是整行长度比长轴，`--cap` 是字高比长轴（鞋垫实测 0.40 和 0.034）。
带字的那个面在底图里要接近正立，否则拟合出来的轴是斜的，叠上去会和透视错位。

改完跟上一版比像素：差异均值应在 1 以内、集中在文字那条窄带；铺满半张图说明长轴拟合跑偏了。
不想保留这行字时，把印字区转到画面边缘裁掉更省事，废字比错字安全。加一张印字放大图当参考只改善符号形状，改不了拼写。

## 交付

报告只给成品路径、改了什么、候选在哪，不贴过程。
项目目录里只放原料和成品：脚本都在 skill 里，不在项目里留临时 `.py`、备份或 `__pycache__`。
产物按「一个入口 + 一个脚本 + 一份 README」收：不为一行依赖单建 requirements，不留能力重叠的第二个脚本，
样例只留一份。合并或精简之后要用可复现的比对证明行为没变（本类就是 `ImageChops.difference` 与上一版成品比，均值 <1 且差异只在文字带）。

## 已知问题

- 出图串行，一张 16~25 秒；接口单并发，并发会 429。
- 模型对几十像素的小字不可控，只能叠印；加参考图对拼写无效。
- 看图模型起草的规格需要人过一遍，尤其是 camera 和 forbid。
- `HTTP 400/404/422` 先怀疑模型不支持图生图，其次才是参数。
- Pillow 只有 `overlay` 需要，模块顶部 try/except 导入，缺的时候只让 overlay 报错，不能拖累 scan 和 render。
- 中文串写完 grep 一次乱码字节（U+FFFD），会原样进提示词。
- 看图模型有时只返回 reasoning 没有 content，取值要两个都兜住；单次 2~3 分钟，超时给 300s 并重试两次，单个 SKU 失败不要整批崩。
