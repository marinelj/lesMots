// Thin wx.cloud.callContainer wrapper: JSON + Bearer-token auth against the
// LesMots API on 微信云托管. callContainer routes over WeChat's internal
// channel, so no request-domain whitelist and no ICP filing are needed.
const CLOUD_ENV = 'prod-d4gd4usjk0c9578c8';
const SERVICE = 'flask-28cx';

let cloudReady = false;
function ensureCloud() {
  if (!cloudReady) {
    wx.cloud.init({ env: CLOUD_ENV });
    cloudReady = true;
  }
}

function request(method, path, body, opts = {}) {
  ensureCloud();
  return new Promise((resolve, reject) => {
    const header = {
      'content-type': 'application/json',
      'X-WX-SERVICE': SERVICE,
    };
    if (!opts.skipAuth) {
      const token = wx.getStorageSync('lesmots_token');
      if (token) header['Authorization'] = `Bearer ${token}`;
    }
    wx.cloud.callContainer({
      config: { env: CLOUD_ENV },
      path,
      method,
      data: method === 'GET' ? undefined : (body || {}),
      header,
      success: res => {
        if (res.statusCode === 401 && !opts.skipAuth) {
          // session expired: re-login once, then retry
          getApp().login()
            .then(() => request(method, path, body, opts))
            .then(resolve, reject);
          return;
        }
        resolve(res.data);
      },
      fail: reject,
    });
  });
}

module.exports = {
  get: (path, opts) => request('GET', path, null, opts),
  post: (path, body, opts) => request('POST', path, body, opts),
};
