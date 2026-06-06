import http from "k6/http";
import { check } from "k6";
import { Trend, Rate, Counter } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL || "http://127.0.0.1:8000";
const ENDPOINT = __ENV.ENDPOINT || "/hash-seq";
const PAYLOAD  = __ENV.PAYLOAD  || '{"text":"benchmark","iterations":10000}';

const reqDuration   = new Trend("request_duration", true);
const errorRate     = new Rate("error_rate");
const totalRequests = new Counter("total_requests");

export const options = {
  noConnectionReuse: true,

  thresholds: {
    "request_duration": ["p(95)<10000", "p(99)<20000"],
    "error_rate":       ["rate<0.1"],
  },
};

export default function () {
  const res = http.post(`${BASE_URL}${ENDPOINT}`, PAYLOAD, {
    headers: { "Content-Type": "application/json" },
  });

  const ok = check(res, {
    "status 200": (r) => r.status === 200,
  });

  reqDuration.add(res.timings.duration);
  errorRate.add(!ok);
  totalRequests.add(1);
}

export function setup() {
  const res = http.get(`${BASE_URL}/health`);
  if (res.status !== 200) {
    throw new Error(`Health check failed: ${BASE_URL}/health`);
  }
}
