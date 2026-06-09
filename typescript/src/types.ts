/** v0.2.6 (H1) — gateway.ts 拆分出来的共享类型
 */
import * as grpc from "@grpc/grpc-js";
import type { Request } from "express";
import type { IncomingMessage } from "http";

/** 路由模块共享上下文 */
export interface RouteContext {
  runtimeConfig: any;  // 避免循环 import，类型由调用方保证
  grpcClient: any;
  memoryClient: any;
  fuxiProto: any;  // proto-loader 加载的模块
  config: any;
}

/** 来自 gRPC streamComplete 的 chunk（最简形状） */
export interface ProtoChunk {
  getContent?: () => string;
  getIsFinal?: () => boolean;
  content?: string;
  is_final?: boolean;
}

/** 来自 gRPC InvokeTool 的 response（最简形状） */
export interface ProtoToolResponse {
  getResultJson?: () => string;
  getSuccess?: () => boolean;
  getElapsedMs?: () => number | string;
  getError?: () => string;
  result_json?: string;
  success?: boolean;
  elapsed_ms?: number | string;
  error?: string;
}

/** 来自 gRPC Memory 的 response */
export interface ProtoMemoryResponse {
  getMemoryContent?: () => string;
  getCharCount?: () => number;
  getEntriesList?: () => Array<{
    getId?: () => string;
    getContent?: () => string;
    getTimestamp?: () => number;
  }>;
  getMemoriesList?: () => Array<{
    getId?: () => string;
    getContent?: () => string;
    getSimilarity?: () => number;
  }>;
  getSuccess?: () => boolean;
  getId?: () => string;
  getError?: () => string;
  memory_content?: string;
  char_count?: number;
  success?: boolean;
  id?: string;
  error?: string;
}

/** 来自 gRPC Heartbeat 的 response */
export interface ProtoHeartbeatResponse {
  getAlive?: () => boolean;
  getTimestamp?: () => number;
  alive?: boolean;
  timestamp?: number;
}
