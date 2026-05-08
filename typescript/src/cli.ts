#!/usr/bin/env node
/** CLI 对话窗口 - 持续对话模式 */
import * as readline from 'readline';
import axios from 'axios';

const rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  prompt: '伏羲 > ',
});

// 加载环境变量
try {
  const dotenv = require("dotenv");
  dotenv.config();
} catch(e) {
  // dotenv 可能不存在，忽略
}

// 配置
const HTTP_PORT = parseInt(process.env.HTTP_PORT || "18789", 10);
const MODEL = process.env.DEFAULT_MODEL || "deepseek-v4-pro";

// 状态
const session_id = `cli-${Date.now()}`;
const history: Array<{role: string, content: string}> = [];
let multilineBuffer = "";
let isMultiline = false;

console.log('\n================================================');
console.log(' 伏羲 CLI 对话窗口 - 持续对话模式');
console.log(' 输入 "help" 查看命令，"exit" 退出');
console.log('================================================\n');

/** 打印帮助 */
function printHelp() {
  console.log(`
可用命令：
  help       - 显示此帮助信息
  clear      - 清空对话历史
  exit/quit  - 退出程序
  status     - 显示当前会话状态

直接输入内容即可对话，换行输入 \\\\ 可继续输入多行。
`);
}

/** 清空历史 */
function clearHistory() {
  history.length = 0;
  console.log('对话历史已清空。\n');
}

/** 显示状态 */
function printStatus() {
  console.log(`
会话 ID: ${session_id}
历史条数: ${history.length}
模型: ${runtimeState.model || "（默认）"}
API 地址: http://localhost:${HTTP_PORT}
Base URL: ${runtimeState.baseUrl || "（默认）"}
`);
}

/** 运行时配置（可被 /set 修改） */
const runtimeState = {
  model: "",
  apiKey: "",
  baseUrl: "",
};

/** 处理命令 */
function handleCommand(cmd: string): boolean {
  const trimmed = cmd.trim().toLowerCase();
  if (trimmed === 'help') {
    printHelp();
    return true;
  } else if (trimmed === 'clear') {
    clearHistory();
    return true;
  } else if (trimmed === 'status') {
    printStatus();
    return true;
  } else if (trimmed === 'set') {
    interactiveSet();
    return true;
  } else if (trimmed === 'exit' || trimmed === 'quit') {
    console.log('\n再见！');
    rl.close();
    process.exit(0);
  }
  return false;
}

/** 发送消息到后端 */
async function sendMessage(content: string): Promise<void> {
  try {
    const payload: any = { message: content, session_id };
    if (runtimeState.model) payload.model = runtimeState.model;
    if (runtimeState.apiKey) payload.apiKey = runtimeState.apiKey;
    if (runtimeState.baseUrl) payload.baseUrl = runtimeState.baseUrl;
    if (history.length > 0) payload.history = history;

    const headers: any = {};
    if (runtimeState.apiKey) headers['Authorization'] = `Bearer ${runtimeState.apiKey}`;
    if (runtimeState.baseUrl) headers['X-Base-Url'] = runtimeState.baseUrl;

    const resp = await axios.post(`http://localhost:${HTTP_PORT}/chat`, payload, {
      headers: Object.keys(headers).length ? headers : undefined,
      timeout: 60000,
    });

    const reply = resp.data?.data?.content || resp.data?.error || '（无回复）';
    history.push({ role: 'user', content });
    history.push({ role: 'assistant', content: reply });

    console.log('\n伏羲:', reply);
  } catch (err: any) {
    console.error('\n错误:', err.message);
    if (err.response) {
      console.error('服务端响应:', err.response.data?.error || err.response.statusText);
    }
  }
}

/** 处理输入行 */
async function processInput(line: string): Promise<void> {
  const trimmed = line.trim();

  // 跳过空行（非多行模式）
  if (!trimmed && !isMultiline) {
    rl.prompt();
    return;
  }

  // 多行模式：检查是否继续
  if (isMultiline) {
    if (trimmed.endsWith('\\')) {
      multilineBuffer += trimmed.slice(0, -1) + '\n';
    } else {
      multilineBuffer += trimmed;
      isMultiline = false;
      const content = multilineBuffer.trim();
      multilineBuffer = "";
      if (content) {
        await sendMessage(content);
      }
    }
    rl.prompt();
    return;
  }

  // 检查多行开始（以 \\ 结尾）
  if (trimmed.endsWith('\\')) {
    multilineBuffer = trimmed.slice(0, -1);
    isMultiline = true;
    rl.setPrompt('... ');
    rl.prompt();
    return;
  }

  // 重置 prompt
  rl.setPrompt('伏羲 > ');

  // 处理命令或消息
  if (trimmed && !handleCommand(trimmed)) {
    await sendMessage(trimmed);
  }

  rl.prompt();
}

/** 交互式设置 */
async function interactiveSet() {
  const rl2 = readline.createInterface({ input: process.stdin, output: process.stdout });

  const ask = (q: string): Promise<string> => new Promise(res => rl2.question(q, ans => res(ans)));

  console.log("\n--- 设置 ---");
  const model = await ask(`模型 [${runtimeState.model || MODEL}]: `);
  const baseUrl = await ask(`Base URL [${runtimeState.baseUrl || '（空）'}]: `);
  const apiKey = await ask(`API Key [***]: `);

  if (model.trim()) runtimeState.model = model.trim();
  if (baseUrl.trim()) runtimeState.baseUrl = baseUrl.trim();
  if (apiKey.trim()) runtimeState.apiKey = apiKey.trim();

  console.log("\n✓ 已更新，当前配置：");
  console.log(`  模型: ${runtimeState.model || MODEL}`);
  console.log(`  Base URL: ${runtimeState.baseUrl || '（默认）'}`);
  console.log(`  API Key: ${runtimeState.apiKey ? '***' + runtimeState.apiKey.slice(-4) : '（默认）'}`);

  rl2.close();
  rl.setPrompt("伏羲 > ");
}

// 输入处理
rl.on('line', (line) => {
  processInput(line);
});

rl.on('close', () => {
  console.log('\n再见！');
  process.exit(0);
});

rl.prompt();