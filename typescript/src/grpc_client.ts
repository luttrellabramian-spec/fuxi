/** gRPC 客户端封装 */
import * as grpc from "@grpc/grpc-js";
import { config } from "./config";

// @ts-ignore - generated proto file, no types available
import * as fuxiProto from "../src/proto/fuxi_pb.js";
// @ts-ignore - generated grpc file, no types available
import { FuxiCoreClient, MemoryServiceClient } from "../src/proto/fuxi_grpc_pb.js";

// 验证 proto 加载成功 - 检查消息类型存在即可
if (!fuxiProto.ToolRequest || !fuxiProto.CompletionRequest) {
  console.error("Failed to load proto messages from fuxi.proto");
  throw new Error("Proto messages not found");
}

// 验证服务客户端存在
if (!FuxiCoreClient || !MemoryServiceClient) {
  console.error("Failed to load gRPC service clients");
  throw new Error("gRPC service clients not found");
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

export { grpcClient, memoryClient, fuxiProto };