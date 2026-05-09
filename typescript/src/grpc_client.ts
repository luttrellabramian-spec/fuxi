/** gRPC 客户端封装 */
import * as grpc from "@grpc/grpc-js";
import { config } from "./config";

// @ts-ignore - generated proto file, no types available
import fuxiProto from "../src/proto/fuxi_pb.js";
// @ts-ignore - generated grpc file, no types available
import { FuxiCoreClient, MemoryServiceClient } from "../src/proto/fuxi_grpc_pb.js";

// 服务定义
const services = fuxiProto as any;

// 验证 proto 加载成功（检查消息类型，而不是服务定义）
const requiredMessages = ['ToolRequest', 'ToolResult', 'CompletionRequest', 'CompletionChunk', 
                          'SessionPing', 'SessionPong', 'HotQuery', 'HotResult', 
                          'WarmQuery', 'WarmResult', 'ColdQuery', 'ColdResult',
                          'MemoryWrite', 'PersistResult', 'MemoryEntry', 'SemanticMemory'];
const missingMessages = requiredMessages.filter(msg => !services[msg]);
if (missingMessages.length > 0) {
  console.error("Failed to load proto messages from fuxi.proto");
  console.error("Missing messages:", missingMessages);
  console.error("Available keys:", Object.keys(services));
  throw new Error(`Proto messages not found: ${missingMessages.join(', ')}`);
}

// 创建 gRPC 客户端
const creds = config.grpcUseTls
  ? grpc.credentials.createSsl()
  : grpc.credentials.createInsecure();

const grpcClient = new FuxiCoreClient(
  `${config.grpcHost}:${config.grpcPort}`,
  creds
);

const memoryClient = new MemoryServiceClient(
  `${config.grpcHost}:${config.grpcPort}`,
  creds
);

export { grpcClient, memoryClient };