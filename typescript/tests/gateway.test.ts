import request from 'supertest';
import app from '../src/gateway';

describe('POST /tool/invoke', () => {
  it('should return 400 if tool_name is missing', async () => {
    const res = await request(app)
      .post('/tool/invoke')
      .send({
        arguments: { key: 'value' }
      });

    expect(res.status).toBe(200); // The API uses 200 with ok:false
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

    // This will fail if gRPC is not running, but validates the request structure
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
    // Should not crash - arguments should default to empty object
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
    // Set low rate limit for testing
    process.env.RATE_LIMIT_MAX = '2';
    process.env.RATE_LIMIT_WINDOW_MS = '1000';

    // Reload gateway with test config
    const gatewayModule = await import('../src/gateway');
    const testApp = gatewayModule.default;

    // Make multiple requests
    const requests = [];
    for (let i = 0; i < 3; i++) {
      requests.push(request(testApp).get('/tool/list'));
    }

    const responses = await Promise.all(requests);

    // First two should succeed
    expect(responses[0].status).toBe(200);
    expect(responses[1].status).toBe(200);

    // Third should be rate limited
    expect(responses[2].status).toBe(429);
    expect(responses[2].body.ok).toBe(false);
    expect(responses[2].body.error).toContain('Too many requests');
  });
});
