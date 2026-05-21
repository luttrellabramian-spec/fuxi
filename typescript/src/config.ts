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

/** 加载 YAML 配置文件 */
function loadYamlConfig(): any {
  try {
    const yaml = require('js-yaml');
    const fs = require('fs');
    const path = require('path');
    
    // 尝试加载 config/default.yaml
    const configPath = path.join(__dirname, '..', '..', 'config', 'default.yaml');
    if (fs.existsSync(configPath)) {
      const configContent = fs.readFileSync(configPath, 'utf8');
      return yaml.load(configContent) || {};
    }
  } catch(e) {
    // js-yaml 可能不存在或配置文件不存在，忽略
  }
  return {};
}

// 加载 YAML 配置
const yamlConfig = loadYamlConfig();

/** 网关配置 - 用户可自由配置 */
export const config = {
  // gRPC 服务地址
  grpcHost: process.env.GRPC_HOST || yamlConfig.grpc?.host || "localhost",
  grpcPort: parseInt(process.env.GRPC_PORT || yamlConfig.grpc?.port?.toString() || "50051", 10),

  // HTTP 网关端口
  httpPort: parseInt(process.env.HTTP_PORT || yamlConfig.gateway?.port?.toString() || "18789", 10),

  // 是否使用 TLS 连接 gRPC（生产环境建议开启）
  grpcUseTls: process.env.GRPC_USE_TLS === "true" || yamlConfig.grpc?.tls === true,

  // 速率限制
  rateLimit: {
    windowMs: parseInt(process.env.RATE_LIMIT_WINDOW_MS || yamlConfig.gateway?.rate_limit?.window_ms?.toString() || "900000", 10), // 15 分钟窗口
    max: parseInt(process.env.RATE_LIMIT_MAX || yamlConfig.gateway?.rate_limit?.max?.toString() || "100", 10), // 每窗口最多请求数
  },

  // LLM API 配置（可选，会被请求中携带的 Authorization 覆盖）
  // 用户可在请求头中传递自己的 API key，gateway 会转发给 gRPC 服务
    auth: {
        // 是否强制验证 API key
        enabled: process.env.AUTH_ENABLED === 'true' || (yamlConfig.auth?.enabled === true && process.env.AUTH_ENABLED !== 'false'),
        // 默认 API key（服务启动时使用，未提供 Authorization header 时生效）
        apiKey: process.env.LLM_API_KEY || yamlConfig.llm?.api_key || "",
        // 默认 base_url（未提供 base-url header 时生效）
        baseUrl: process.env.LLM_BASE_URL || yamlConfig.llm?.base_url || "",
        // 默认模型
        model: process.env.DEFAULT_MODEL || process.env.LLM_MODEL || yamlConfig.llm?.model || "",
    },
};