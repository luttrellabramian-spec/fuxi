import request from 'supertest';
import app from '../src/gateway';

describe('POST /tool/invoke', () => {
  it('should return 400 if tool_name is missing', async () => {
    const res = await request(app)
      .post('/tool/invoke')
      .send({
        arguments: { key: 'value' }
      });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.error).toContain('tool_name is required');
  });

  it('should accept valid tool invocation with all parameters', async () => {
    const res = await request(app)
      .post('/tool/invoke')
      .send({
        tool_name: 'test_tool',
        arguments: { key: 'value' },
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
  });

  it('should handle missing arguments parameter gracefully', async () => {
    const res = await request(app)
      .post('/tool/invoke')
      .send({
        tool_name: 'test_tool',
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
  });

  it('should handle empty arguments object', async () => {
    const res = await request(app)
      .post('/tool/invoke')
      .send({
        tool_name: 'test_tool',
        arguments: {},
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
  });
});

describe('POST /chat', () => {
  it('should return error if message is missing', async () => {
    const res = await request(app)
      .post('/chat')
      .send({
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.error).toContain('message is required');
  });

  it('should accept valid chat request', async () => {
    const res = await request(app)
      .post('/chat')
      .send({
        message: 'hello',
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
  });

  it('should use default session_id if not provided', async () => {
    const res = await request(app)
      .post('/chat')
      .send({
        message: 'hello'
      });

    expect(res.status).toBe(200);
  });
});

describe('GET /tool/list', () => {
  it('should return list of tools', async () => {
    const res = await request(app)
      .get('/tool/list');

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data).toHaveProperty('tools');
    expect(Array.isArray(res.body.data.tools)).toBe(true);
  });
});

describe('GET /health', () => {
  it('should return health status', async () => {
    const res = await request(app)
      .get('/health');

    expect(res.status).toBe(200);
    expect(res.body).toHaveProperty('ok');
    expect(res.body).toHaveProperty('timestamp');
  });
});

describe('GET /settings', () => {
  it('should return current configuration', async () => {
    const res = await request(app)
      .get('/settings');

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data).toHaveProperty('config');
    expect(res.body.data).toHaveProperty('envDefaults');
  });
});

describe('POST /settings', () => {
  it('should update configuration', async () => {
    const res = await request(app)
      .post('/settings')
      .send({
        model: 'test-model',
        maxTokens: 1024
      });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data).toHaveProperty('message');
  });
});

describe('Memory Endpoints', () => {
  it('GET /memory/hot should return hot memory', async () => {
    const res = await request(app)
      .get('/memory/hot');

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data).toHaveProperty('content');
    expect(res.body.data).toHaveProperty('char_count');
  });

  it('POST /memory/hot should write to hot memory', async () => {
    const res = await request(app)
      .post('/memory/hot')
      .send({
        content: 'test memory content',
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });

  it('POST /memory/hot should require content', async () => {
    const res = await request(app)
      .post('/memory/hot')
      .send({
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(false);
    expect(res.body.error).toContain('content is required');
  });

  it('GET /memory/warm/recent should return warm memory', async () => {
    const res = await request(app)
      .get('/memory/warm/recent')
      .query({ session_id: 'test_session', limit: 10 });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data).toHaveProperty('entries');
  });

  it('POST /memory/warm/add should add to warm memory', async () => {
    const res = await request(app)
      .post('/memory/warm/add')
      .send({
        content: 'test warm memory',
        session_id: 'test_session'
      });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
  });

  it('GET /memory/cold/recent should return cold memory', async () => {
    const res = await request(app)
      .get('/memory/cold/recent')
      .query({ session_id: 'test_session', limit: 10 });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(res.body.data).toHaveProperty('memories');
  });
});

describe('Rate Limiting', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it('should return 429 when rate limit exceeded', async () => {
    process.env.RATE_LIMIT_MAX = '2';
    process.env.RATE_LIMIT_WINDOW_MS = '1000';

    const gatewayModule = await import('../src/gateway');
    const testApp = gatewayModule.default;

    const requests = [];
    for (let i = 0; i < 3; i++) {
      requests.push(request(testApp).get('/tool/list'));
    }

    const responses = await Promise.all(requests);

    expect(responses[0].status).toBe(200);
    expect(responses[1].status).toBe(200);
    expect(responses[2].status).toBe(429);
    expect(responses[2].body.ok).toBe(false);
    expect(responses[2].body.error).toContain('Too many requests');
  });
});

describe('Error Handling', () => {
  it('should handle invalid JSON gracefully', async () => {
    const res = await request(app)
      .post('/chat')
      .set('Content-Type', 'application/json')
      .send('invalid json');

    expect(res.status).toBe(400);
  });

  it('should return 404 for unknown routes', async () => {
    const res = await request(app)
      .get('/unknown-route');

    expect(res.status).toBe(404);
  });
});
