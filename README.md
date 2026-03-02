# Hot News Ranker (CN) — GitHub Pages 自动站点

本仓库通过 GitHub Actions **每日定时**抓取权威来源的国内新闻，评分排序后生成 **静态网页 `index.html`** 并通过 **GitHub Pages** 发布。

## 本地调试
```bash
pip install -r requirements.txt
python cn_hot_news_ranker3.py --html index.html --no-txt --no-docx --outdir .
```
打开 `index.html` 预览。

## 说明
- 每站最多抓取 3 条，跨站去重；
- 每条包含一行 **概括性摘要**（RSS → 页面 meta → 正文首段 → 标题回退）；
- 输出仅为静态文件，无后端依赖，适合 Pages 托管。
