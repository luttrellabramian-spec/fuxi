const grpc = require('@grpc/grpc-js');
const fuxiProto = require('./src/proto/fuxi_pb.js');
const { FuxiCoreClient } = require('./src/proto/fuxi_grpc_pb.js');

const creds = grpc.credentials.createInsecure();
const client = new FuxiCoreClient('localhost:50051', creds);

const metadata = new grpc.Metadata();
metadata.add('authorization', 'Bearer test');

const request = new fuxiProto.CompletionRequest();
request.setUserMessage("What is 2+2?");
request.setModel("test");
request.setSessionId("test");

const call = client.streamComplete(request, metadata);
let chunks = [];

call.on('data', (chunk) => {
    console.log('data event:', JSON.stringify(chunk));
    console.log('chunk.content:', chunk.content);
    console.log('chunk.is_final:', chunk.is_final);
    chunks.push(chunk);
});

call.on('end', () => {
    console.log('end event, total chunks:', chunks.length);
    let fullContent = chunks.map(c => c.content).join('');
    console.log('fullContent:', fullContent);
    call.cancel();
});

call.on('error', (err) => {
    console.error('error:', err.message);
});

setTimeout(() => {
    console.log('Timeout, cancelling');
    call.cancel();
    process.exit(1);
}, 10000);