# aliexpress-product-images

从一批商品素材批量生成速卖通（AliExpress）商品主图。每个 SKU 给一张产品图、一张效果参考图，
输出 1:1、2048×2048 的 JPEG：产品本体来自产品图，效果图只提供机位、光线、背景和构图。

本仓库同时是一个 Agent Skill，Claude Code、Hermes 或任何能执行命令行的 agent 都能直接调用，
也可以当普通命令行工具用。

## 模型要求

出图走图生图的图像编辑接口：`POST {api_base}/images/edits`，请求里同时带产品图和效果图。

**必须是支持图生图 / 图像编辑的模型**，也就是能接收 `images` 输入、按提示词改机位的模型。
纯文生图模型（只吃 prompt）用不了；各家叫法不同，常见的是 *image edit*、*image-to-image*、
*reference image* 一类的接口。没有这类接口时，本工具没有别的降级路径。

模型和端点由使用者自己填，仓库里没有绑定任何一家：

```bash
export AXN_API_BASE=https://你的端点/v1        # OpenAI 兼容
export AXN_API_KEY=sk-...
export AXN_IMAGE_MODEL=你的图生图模型
export AXN_VLM_MODEL=你的看图模型              # 可选，只有 spec / auto 起草提示词时需要
```

或者在项目根放 `.axn.json`（优先级高于环境变量）：

```json
{
  "api_base": "https://你的端点/v1",
  "image_model": "你的图生图模型",
  "vlm_model": "你的看图模型",
  "size": "2048x2048",
  "api_key_env": "MY_KEY_VAR"
}
```

密钥只从环境变量读，不写进配置文件。`api_key_env` 用来指定读哪个环境变量名。

## 安装

```bash
git clone https://github.com/jiawenlee1204/aliexpress-product-images.git
cd aliexpress-product-images
bash install.sh
```

`install.sh` 会把目录复制到检测到的 agent 位置，也可以明确指定：

```bash
bash install.sh --target claude                                        # ~/.claude/skills/
bash install.sh --target hermes                                        # ~/.hermes/skills/creative/
bash install.sh --target ~/my-agents/skills                            # 任意目录
```

叠印功能需要 Pillow：`pip install Pillow`。其它子命令只用标准库。

## 用法

来货目录：

```
新/袜子图片方位需求/
   袜子产品方位需求.xlsx      A 列 SKU，B 列机位要求，可以没有
   SK0020603/
      SK0020603产品图.jpg    产品图，唯一真实性来源
      SK0020603效果图.jpg    效果参考，只借鉴机位 / 背景 / 光 / 构图
```

在来货目录的上一级执行：

```bash
S=~/aliexpress-product-images/scripts/axn.py     # 或 $HOME/.claude/skills/aliexpress-product-images/scripts/axn.py

python3 $S scan 新                                # 列出 SKU 与 Excel 机位要求
python3 $S spec --src 新                           # 看图模型起草六段提示词
$EDITOR _work/specs.json                           # 手改 camera / forbid，这一步决定质量
python3 $S render --specs _work/specs.json --src 新 --dry-run
python3 $S render --specs _work/specs.json --src 新
python3 $S pick --sku SK0020603 --cand A           # 定稿进 output/
python3 $S clean                                   # 把过程文件收进 _work/
```

不想分步时可以一条命令跑完：

```bash
python3 $S auto --src 新 --review
```

`auto` 的提示词由看图模型起草，产品识别和机位判断不如人工，出货前建议仍然手改一次 camera。

## 项目目录

```
output/            成品 <SKU>.jpg
_work/candidates/  候选 <SKU>_<候选>.jpg
_work/replaced/    被替换的旧成品
_work/specs.json   提示词规格
```

## 提示词规格

提示词固定六段：`identity`、`camera`、`adapt`、`allow`、`forbid`、`tail`。
前两段连同 adapt 拼成主体要求，allow 与 tail 拼成白名单和最后一道数量锁。
每个 SKU 可以放多个候选（`A`、`B`…），同一套装配关系、不同机位强调。

```json
{
  "skus": {
    "SK0020603": {
      "A": {
        "identity": "一双黑色中筒袜……共 2 只",
        "camera": "真人赤脚穿着……产品占画面约 75%",
        "adapt": "纯黑袜子容易糊成死黑，用亮背景加轮廓光……",
        "allow": "穿着同款袜子的两只脚、浅色木地板",
        "forbid": "鞋子、第二双袜子、文字",
        "tail": "两只必须是同一双，袜底平滑无花"
      }
    }
  }
}
```

可选键：`refs` 放额外参考图（相对该 SKU 目录，如印字局部放大，能改善符号形状，改不了拼写）；
`overlay` 指定出图后自动叠印的参数。写法经验和返工对照见 `references/prompt-playbook.md`。

## 产品印字叠印

模型把几十像素的小字当纹理画，拼写不可控。做法是先出一张表面干净、禁止任何文字的底图，再叠印：

```bash
python3 $S overlay --base 底图.jpg --out 成品.jpg --text "COMFORT|INSOLE"
```

脚本找主体像素、用 PCA 拟合长轴，沿长轴从后跟侧排字，再 multiply 上去保留底材纹理。
`--text` 里 `|` 之间留空是插入符号，默认画空心圆角方形波浪边印章。

比例按真品量取：`--len` 是整行长度比长轴，`--cap` 是字高比长轴（一种鞋垫实测 0.40 和 0.034）。
带字的那一面在底图里要接近正立，否则拟合出的轴是斜的，叠上去会和透视错位。

## 已知限制

- 出图串行，单张 16~25 秒；接口单并发，并发会返回 429。
- 产品上的小字只能叠印，加参考图对拼写无效。
- `auto` / `spec` 依赖看图模型，识别精度低于人工。
- 需要联网与可用 Key；脚本默认绕过系统代理直连。
- `HTTP 400 / 404 / 422` 通常意味着模型不支持图生图，其次才检查参数。

## 目录

```
SKILL.md                       agent 读的流程与经验
install.sh                     安装
scripts/axn.py                 全部子命令
references/prompt-playbook.md  六段写法与返工对照
references/example-specs.json  一份真实规格，照着改
```

## 许可

MIT，见 LICENSE。
