// 环境变量覆盖（自动加载）
try {
  const dotenv = require("dotenv");
  dotenv.config();
} catch(e) {
  // dotenv 可能不存在，忽略
}

/** 手动加载环境变量（可被 main 调用） */
export function loadEnv() {
  try {
    const dotenv = require("dotenv");
    dotenv.config();
  } catch(e) {
    // dotenv 可能不存在，忽略
  }
}

/** 网关配置 - 用户可自由配置 */
export const config = {
  // gRPC 服务地址
  grpcHost: process.env.GRPC_HOST || "localhost",
  grpcPort: parseInt(process.env.GRPC_PORT || "50051", 10),

  // HTTP 网关端口
  httpPort: parseInt(process.env.HTTP_PORT || "18789", 10),

  // 是否使用 TLS 连接 gRPC（生产环境建议开启）
  grpcUseTls: process.env.GRPC_USE_TLS === "true",

  // 速率限制
  rateLimit: {
    windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS || "900000", 10), // 15 分钟窗口
    max: parseInt(process.env.RATE_LIMIT_MAX || "100", 10), // 每窗口最多请求数
  },

  // LLM API 配置（可选，会被请求中携带的 Authorization 覆盖）
  // 用户可在请求头中传递自己的 API key，gateway 会转发给 gRPC 服务
  auth: {
    // 是否强制验证 API key
    enabled: process.env.AUTH_ENABLED !== "false",
    // 默认 API key（服务启动时使用，未提供 Authorization header 时生效）
    apiKey: process.env.DEEPSEEK_API_KEY || "",
    // 默认 base_url（未提供 base-url header 时生效）
    baseUrl: process.env.DEEPSEEK_BASE_URL || "",
  },
};