---
banner: "![[ https://api.dujin.org/bing/1366.php]]"
banner_y: 0.2406
banner_x: 0.48434
banner_lock: false
---
#homepage
# 1 🏠 HomePage
```dataviewjs
let ftMd = dv.pages("").file.sort(t => t.cday)[0]
let total = parseInt([new Date() - ftMd.ctime] / (60*60*24*1000))
let totalDays = " 您已使用 *Obsidian* "+total+" 天，"
let nofold = '!"misc/templates"'
let allFile = dv.pages(nofold).file
let totalMd = "共创建 "+
	allFile.length+" 篇笔记"
let totalTag = allFile.etags.distinct().length+" 个标签"
dv.paragraph(
	totalDays+totalMd+"、"+totalTag+""
)
```
## 1.1 🔖 快速导航

## 1.2 ✅ 今天的任务（自动）

## 1.3 📊 日记统计

```dataviewjs
let templateContent = await dv.io.load("Template/daily dairy template.md");
templateContent = templateContent.replace(/^---[\s\S]*?---\n?/, "");
let templateCharCount = templateContent.replace(/\s/g, "").length;

let dairyFiles = dv.pages('"nodes/dairy"').file;
let dairyCount = dairyFiles.length;

let totalChars = 0;
for (let f of dairyFiles) {
    let content = await dv.io.load(f.path);
    content = content.replace(/^---[\s\S]*?---\n?/, "");
    totalChars += content.replace(/\s/g, "").length;
}

let actualChars = totalChars - templateCharCount * dairyCount;

dv.paragraph(`共 ${dairyCount} 篇日记，总字数 ${actualChars} 字`);
```

## 1.4 🔥 日记热力图

```dataviewjs
const diaryMap = new Map();
const files = dv.pages('"nodes/dairy"').file;
for (const f of files) {
    const content = await dv.io.load(f.path);
    const m = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
    if (m) {
        const dm = m[1].match(/date:\s*"?(\d{4}-\d{2}-\d{2})"?/);
        if (dm) diaryMap.set(dm[1], f.path);
    }
}

for (let year = 2026; year >= 2021; year--) {
    const entries = [...diaryMap.keys()]
        .filter(d => d.startsWith(String(year)))
        .map(d => ({
            date: d,
        }));

    const wrapper = this.container.createEl('div', {
        attr: { style: 'margin-bottom: 16px;' }
    });
    wrapper.createEl('h4', { text: `${year} 年` });
    const calEl = wrapper.createEl('div');

    renderHeatmapCalendar(calEl, {
        year,
        entries,
        showCurrentDayBorder: false,
        colors: {
            "purple": ["#e8ddf5", "#d4c4f0", "#c4ade6", "#b89de0", "#9d79d6"]
        }
    });

    // 点击方块跳转到对应日记
    const boxes = calEl.querySelectorAll('.heatmap-calendar-boxes li.hasData');
    boxes.forEach(box => {
        const dateStr = box.getAttribute('data-date');
        const filePath = diaryMap.get(dateStr);
        if (filePath) {
            box.style.cursor = 'pointer';
            box.addEventListener('click', (e) => {
                e.stopPropagation();
                app.workspace.openLinkText(filePath, '', false);
            });
        }
    });
}
```
