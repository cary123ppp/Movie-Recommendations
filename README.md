# ReelRank：自动生成电影排行视频

写好一份英文文案，运行一条命令，自动得到两个成品视频：

- `xxx_16x9.mp4`：横屏，用于 YouTube
- `xxx_9x16.mp4`：竖屏，用于 TikTok、Shorts

程序会自动完成：
1. 去 TMDB 查电影信息（片名、年份、导演），去 OMDb 查 IMDb 评分和奥斯卡获奖情况；
2. 找到官方预告片并下载（1080p）；
3. 自动去掉预告片的黑边，挑出能用的镜头，避开片头 logo、结尾字卡和黑场；
4. AI 配音，并生成逐词高亮的字幕；
5. 按 ReelRank 的风格剪辑：电影节风格的片名卡（麦穗、星级、火星粒子）、快节奏混剪、金色字幕；
6. 加上音效、背景低音，音量统一标准化到 -14 LUFS（YouTube 和 TikTok 的推荐响度）。

---

## Windows 最简用法（推荐）

1. 先装 Python 3.11：https://www.python.org/downloads/ ，安装时**勾选 "Add Python to PATH"**。
2. 下载本项目：在 GitHub 页面点绿色的 **Code → Download ZIP**，解压到一个**没有空格、不含中文**的路径，比如 `D:\ReelRank`。会用 git 的话直接 clone 也行。
3. 双击 **`install.bat`**：会自动安装 ffmpeg 和所有依赖。第一次装 ffmpeg 后需要再双击一次。最后它会用记事本打开 `config.yaml`，把 key 填进去保存即可。
4. 写文案：复制 `episodes\war_too_real.yaml` 改成你的内容（用记事本或 VS Code 编辑，保存为 UTF-8）。
5. 把文案文件**拖到对应的图标上**：
   - `check.bat`：检查电影和预告片有没有找对
   - `preview.bat`：几分钟出几张静帧，检查排版
   - `make.bat`：生成成品，完成后自动打开 `output` 文件夹

> 在文案里写 Windows 本地路径时，用单引号或正斜杠，比如 `trailer_file: 'D:\Videos\spr.mp4'` 或 `trailer_file: D:/Videos/spr.mp4`。不要用双引号加反斜杠，YAML 会把它当成转义字符。

下面是命令行方式的详细说明（Mac 也适用）。

---

## 一、安装（只需要做一次）

### 1. 安装 Python 和 ffmpeg

**Mac：**
```bash
# 没有 Homebrew 的话，先去 https://brew.sh 按提示安装
brew install python@3.11 ffmpeg
```

**Windows：**
- Python：到 https://www.python.org/downloads/ 下载 3.11。安装时**一定要勾选 "Add Python to PATH"**。
- ffmpeg：打开 PowerShell，运行 `winget install Gyan.FFmpeg`，装完后重新打开 PowerShell。

检查是否装好：
```bash
python --version     # 应显示 3.10 或更高
ffmpeg -version
```

### 2. 下载项目并安装依赖

```bash
git clone https://github.com/cary123ppp/Movie-Recommendations.git
cd Movie-Recommendations
python -m pip install -r requirements.txt
```

可选：想让字幕高亮和语音对得更准，可以再安装 `python -m pip install faster-whisper`。

### 3. 填写 API key

把 `config.example.yaml` 复制一份，命名为 `config.yaml`，然后填入：

| 字段 | 从哪里拿 | 是否必需 |
|---|---|---|
| `tmdb_token` | themoviedb.org → Settings → API → **API Read Access Token**（很长的那串） | 必需 |
| `omdb_key` | omdbapi.com → API Key → FREE，key 会发到邮箱 | 推荐（用于 IMDb 评分和奥斯卡信息） |
| `voice.elevenlabs_key` / `elevenlabs_voice_id` | elevenlabs.io | 可选（更像真人的付费配音） |

`config.yaml` 已经写在 `.gitignore` 里，**不会被上传到 GitHub**。

第一次运行时，程序会自动下载免费的 AI 配音模型（约 300MB），以后不用再下。

---

## 二、做一期视频

### 1. 写文案

复制 `episodes/war_too_real.yaml`，改成你的内容。格式如下：

```yaml
slug: horror_cant_sleep            # 输出文件名

hook:                              # 开场卡
  overline: "Horror Movies That Will"
  headline: "RUIN YOUR SLEEP"
  left:  ["FIVE FILMS", "RANKED BY FEAR"]
  right: ["WATCH WITH", "LIGHTS ON"]
  vo: "These five horror movies will ruin your sleep. Number one still haunts me."

films:                             # 排名从低写到高，按出场顺序
  - rank: 5
    title: "Hereditary"
    year: 2018                     # 写上年份，避免找错同名电影
    vo: "Number five. Hereditary. ..."
  # ... 一直写到 rank: 1

outro:
  question: ["Which One", "SCARED YOU MOST?"]
  vo: "Which one scared you the most? Tell me in the comments, and follow for more."
```

**文案建议：**
- 每部电影的 `vo` 以 "Number X. 片名." 开头，片名卡出现时正好念到。
- 每部电影写 50～70 个英文单词，大约 15 秒。整期 4～6 部电影，正好 1～2 分钟。
- 数字最好用英文单词写（例如 "Thirteen Hours"），AI 读起来更自然；年份可以直接写数字。

### 2. 先检查电影和预告片是否找对

```bash
python -m reelrank check episodes/horror_cant_sleep.yaml
```

会列出每部电影的年份、导演、评分、获奖信息和预告片链接。如果有找错的，可以在文案里手动指定（见下面的"微调选项"）。

### 3. 先出几张静帧看看（约 3～5 分钟）

```bash
python -m reelrank build episodes/horror_cant_sleep.yaml --preview
```

在 `output/` 文件夹里会生成几张关键画面的截图，用来快速检查排版。

### 4. 生成成品视频

```bash
python -m reelrank build episodes/horror_cant_sleep.yaml
```

成品输出到 `output/` 文件夹。一期 1～2 分钟的视频，普通电脑大约需要 15～30 分钟。
- 只要横屏：加 `--only h`
- 只要竖屏：加 `--only v`

再次运行时，已经下载的预告片和已经生成的配音会直接复用，只有改过的文案会重新配音。

---

## 三、微调选项（写在某部电影下面）

| 选项 | 作用 | 例子 |
|---|---|---|
| `tmdb_id` | 找错电影时，直接指定 TMDB 编号 | `tmdb_id: 857` |
| `trailer_url` | 换一个 YouTube 预告片 | `trailer_url: https://www.youtube.com/watch?v=...` |
| `trailer_file` | 使用本地视频文件 | `trailer_file: ~/Downloads/spr.mp4` |
| `use_range` | 只从预告片的这段时间里取镜头（单位：秒） | `use_range: [10, 120]` |
| `exclude` | 跳过某几段（比如有字幕或不好的镜头） | `exclude: [[45, 52], [80, 84]]` |
| `card_at` | 指定片名卡背景用第几秒的画面 | `card_at: 63.5` |
| `fact` | 片名卡右侧的信息（默认根据奥斯卡信息自动生成） | `fact: ["TRUE STORY", "BENGHAZI, 2012"]` |
| `director` / `imdb` / `year` | 手动覆盖自动查到的信息 | `imdb: 8.6` |

---

## 四、多个账号

每个账号复制一份 `config.yaml`，改 `brand.name`，例如 `FRIGHTRANK`。运行时指定用哪个配置：

```bash
python -m reelrank build episodes/xxx.yaml --config config_horror.yaml
```

建议不同账号再换一个配音声音（`kokoro_voice`，例如 `am_adam`、`bm_george`），避免平台判定为批量重复内容。

背景音乐：把免版权音乐（例如 YouTube 音频库里的）路径填到 `music:`，程序会在有人声时自动压低音乐音量。

---

## 五、常见问题

- **预告片下载失败**：先运行 `python -m pip install -U yt-dlp` 更新下载工具（YouTube 经常改版）。还不行的话，就自己下载视频，用 `trailer_file` 指定本地文件。
- **某个镜头里有字幕或片名字卡**：用 `exclude` 把那几秒排除掉。
- **字幕高亮和声音对不齐**：安装 `faster-whisper`（见安装第 2 步），或者改用 ElevenLabs 配音。

---

## 六、版权提醒

预告片和电影画面的版权属于片方。这个工具的剪法（短镜头、加上自己的解说和点评、配原创图形）属于常见的影评或推荐类二创，但平台仍然可能对个别片段发起版权认领。建议：
- 不要用整段的长镜头；
- 解说要是你自己的观点；
- 背景音乐只用免版权音乐。

---

## 项目结构

```
reelrank/
  movies.py     TMDB / OMDb 查询
  trailer.py    预告片下载、黑边检测、镜头挑选
  voice.py      配音（Kokoro 免费 / ElevenLabs）和字幕时间轴
  pipeline.py   时间线、素材剪切、混音、导出
  render.py     画面渲染（片名卡、混剪版式、字幕）
  ornament.py   麦穗和星星
assets/fonts/   字体（Cinzel、Limelight、Montserrat，均为 SIL 开源字体协议）
episodes/       每期文案
```
