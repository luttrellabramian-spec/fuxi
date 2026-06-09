/**
 * tsc 不会复制非 .ts 文件。
 * 这个脚本把 src/ui/*.html 复制到 dist/ui/，让 readUiTemplate() 的 prod 路径生效。
 */
const fs = require('fs');
const path = require('path');

const projectRoot = path.join(__dirname, '..');
const srcDir = path.join(projectRoot, 'src', 'ui');
const dstDir = path.join(projectRoot, 'dist', 'ui');

if (!fs.existsSync(srcDir)) {
  console.error(`[copy-ui] source not found: ${srcDir}`);
  process.exit(1);
}

fs.mkdirSync(dstDir, { recursive: true });

const files = fs.readdirSync(srcDir).filter(f => f.endsWith('.html'));
for (const f of files) {
  fs.copyFileSync(path.join(srcDir, f), path.join(dstDir, f));
}

console.log(`[copy-ui] copied ${files.length} UI file(s) to dist/ui/`);
