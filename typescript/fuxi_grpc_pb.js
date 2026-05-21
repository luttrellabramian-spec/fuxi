// GENERATED CODE -- DO NOT EDIT!

'use strict';
var grpc = require('grpc');
var fuxi_pb = require('./fuxi_pb.js');

function serialize_fuxi_ColdQuery(arg) {
  if (!(arg instanceof fuxi_pb.ColdQuery)) {
    throw new Error('Expected argument of type fuxi.ColdQuery');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_ColdQuery(buffer_arg) {
  return fuxi_pb.ColdQuery.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_ColdResult(arg) {
  if (!(arg instanceof fuxi_pb.ColdResult)) {
    throw new Error('Expected argument of type fuxi.ColdResult');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_ColdResult(buffer_arg) {
  return fuxi_pb.ColdResult.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_CompletionChunk(arg) {
  if (!(arg instanceof fuxi_pb.CompletionChunk)) {
    throw new Error('Expected argument of type fuxi.CompletionChunk');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_CompletionChunk(buffer_arg) {
  return fuxi_pb.CompletionChunk.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_CompletionRequest(arg) {
  if (!(arg instanceof fuxi_pb.CompletionRequest)) {
    throw new Error('Expected argument of type fuxi.CompletionRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_CompletionRequest(buffer_arg) {
  return fuxi_pb.CompletionRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_HotQuery(arg) {
  if (!(arg instanceof fuxi_pb.HotQuery)) {
    throw new Error('Expected argument of type fuxi.HotQuery');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_HotQuery(buffer_arg) {
  return fuxi_pb.HotQuery.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_HotResult(arg) {
  if (!(arg instanceof fuxi_pb.HotResult)) {
    throw new Error('Expected argument of type fuxi.HotResult');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_HotResult(buffer_arg) {
  return fuxi_pb.HotResult.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_MemoryWrite(arg) {
  if (!(arg instanceof fuxi_pb.MemoryWrite)) {
    throw new Error('Expected argument of type fuxi.MemoryWrite');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_MemoryWrite(buffer_arg) {
  return fuxi_pb.MemoryWrite.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_PersistResult(arg) {
  if (!(arg instanceof fuxi_pb.PersistResult)) {
    throw new Error('Expected argument of type fuxi.PersistResult');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_PersistResult(buffer_arg) {
  return fuxi_pb.PersistResult.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_SessionPing(arg) {
  if (!(arg instanceof fuxi_pb.SessionPing)) {
    throw new Error('Expected argument of type fuxi.SessionPing');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_SessionPing(buffer_arg) {
  return fuxi_pb.SessionPing.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_SessionPong(arg) {
  if (!(arg instanceof fuxi_pb.SessionPong)) {
    throw new Error('Expected argument of type fuxi.SessionPong');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_SessionPong(buffer_arg) {
  return fuxi_pb.SessionPong.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_ToolRequest(arg) {
  if (!(arg instanceof fuxi_pb.ToolRequest)) {
    throw new Error('Expected argument of type fuxi.ToolRequest');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_ToolRequest(buffer_arg) {
  return fuxi_pb.ToolRequest.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_ToolResult(arg) {
  if (!(arg instanceof fuxi_pb.ToolResult)) {
    throw new Error('Expected argument of type fuxi.ToolResult');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_ToolResult(buffer_arg) {
  return fuxi_pb.ToolResult.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_WarmQuery(arg) {
  if (!(arg instanceof fuxi_pb.WarmQuery)) {
    throw new Error('Expected argument of type fuxi.WarmQuery');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_WarmQuery(buffer_arg) {
  return fuxi_pb.WarmQuery.deserializeBinary(new Uint8Array(buffer_arg));
}

function serialize_fuxi_WarmResult(arg) {
  if (!(arg instanceof fuxi_pb.WarmResult)) {
    throw new Error('Expected argument of type fuxi.WarmResult');
  }
  return Buffer.from(arg.serializeBinary());
}

function deserialize_fuxi_WarmResult(buffer_arg) {
  return fuxi_pb.WarmResult.deserializeBinary(new Uint8Array(buffer_arg));
}


var FuxiCoreService = exports.FuxiCoreService = {
  invokeTool: {
    path: '/fuxi.FuxiCore/InvokeTool',
    requestStream: false,
    responseStream: false,
    requestType: fuxi_pb.ToolRequest,
    responseType: fuxi_pb.ToolResult,
    requestSerialize: serialize_fuxi_ToolRequest,
    requestDeserialize: deserialize_fuxi_ToolRequest,
    responseSerialize: serialize_fuxi_ToolResult,
    responseDeserialize: deserialize_fuxi_ToolResult,
  },
  streamComplete: {
    path: '/fuxi.FuxiCore/StreamComplete',
    requestStream: false,
    responseStream: true,
    requestType: fuxi_pb.CompletionRequest,
    responseType: fuxi_pb.CompletionChunk,
    requestSerialize: serialize_fuxi_CompletionRequest,
    requestDeserialize: deserialize_fuxi_CompletionRequest,
    responseSerialize: serialize_fuxi_CompletionChunk,
    responseDeserialize: deserialize_fuxi_CompletionChunk,
  },
  heartbeat: {
    path: '/fuxi.FuxiCore/Heartbeat',
    requestStream: false,
    responseStream: false,
    requestType: fuxi_pb.SessionPing,
    responseType: fuxi_pb.SessionPong,
    requestSerialize: serialize_fuxi_SessionPing,
    requestDeserialize: deserialize_fuxi_SessionPing,
    responseSerialize: serialize_fuxi_SessionPong,
    responseDeserialize: deserialize_fuxi_SessionPong,
  },
};

exports.FuxiCoreClient = grpc.makeGenericClientConstructor(FuxiCoreService, 'FuxiCore');
var MemoryServiceService = exports.MemoryServiceService = {
  queryHot: {
    path: '/fuxi.MemoryService/QueryHot',
    requestStream: false,
    responseStream: false,
    requestType: fuxi_pb.HotQuery,
    responseType: fuxi_pb.HotResult,
    requestSerialize: serialize_fuxi_HotQuery,
    requestDeserialize: deserialize_fuxi_HotQuery,
    responseSerialize: serialize_fuxi_HotResult,
    responseDeserialize: deserialize_fuxi_HotResult,
  },
  queryWarm: {
    path: '/fuxi.MemoryService/QueryWarm',
    requestStream: false,
    responseStream: false,
    requestType: fuxi_pb.WarmQuery,
    responseType: fuxi_pb.WarmResult,
    requestSerialize: serialize_fuxi_WarmQuery,
    requestDeserialize: deserialize_fuxi_WarmQuery,
    responseSerialize: serialize_fuxi_WarmResult,
    responseDeserialize: deserialize_fuxi_WarmResult,
  },
  queryCold: {
    path: '/fuxi.MemoryService/QueryCold',
    requestStream: false,
    responseStream: false,
    requestType: fuxi_pb.ColdQuery,
    responseType: fuxi_pb.ColdResult,
    requestSerialize: serialize_fuxi_ColdQuery,
    requestDeserialize: deserialize_fuxi_ColdQuery,
    responseSerialize: serialize_fuxi_ColdResult,
    responseDeserialize: deserialize_fuxi_ColdResult,
  },
  persistMemory: {
    path: '/fuxi.MemoryService/PersistMemory',
    requestStream: false,
    responseStream: false,
    requestType: fuxi_pb.MemoryWrite,
    responseType: fuxi_pb.PersistResult,
    requestSerialize: serialize_fuxi_MemoryWrite,
    requestDeserialize: deserialize_fuxi_MemoryWrite,
    responseSerialize: serialize_fuxi_PersistResult,
    responseDeserialize: deserialize_fuxi_PersistResult,
  },
};

exports.MemoryServiceClient = grpc.makeGenericClientConstructor(MemoryServiceService, 'MemoryService');
