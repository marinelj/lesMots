// 兴趣: feed topics — the interests that seed the daily story.
const api = require('../../utils/api');

Page({
  data: {
    interests: [],
    language: '',
    draft: '',
    dirty: false,
    saving: false,
    error: '',
  },

  onShow() {
    api.get('/api/config')
      .then(cfg => this.setData({
        interests: cfg.interests || [],
        language: cfg.language || '',
        dirty: false,
        error: '',
      }))
      .catch(() => this.setData({ error: '加载失败，请重进本页' }));
  },

  onDraft(e) {
    this.setData({ draft: e.detail.value });
  },

  addInterest() {
    const topic = this.data.draft.trim();
    if (!topic) return;
    if (this.data.interests.includes(topic)) {
      this.setData({ draft: '' });
      return;
    }
    this.setData({
      interests: [...this.data.interests, topic],
      draft: '',
      dirty: true,
    });
  },

  removeInterest(e) {
    const interests = this.data.interests.slice();
    interests.splice(e.currentTarget.dataset.index, 1);
    this.setData({ interests, dirty: true });
  },

  save() {
    if (!this.data.dirty || this.data.saving) return;
    this.setData({ saving: true, error: '' });
    api.post('/api/interests', { interests: this.data.interests })
      .then(res => {
        if (res.error) {
          this.setData({ error: res.error, saving: false });
          return;
        }
        this.setData({ saving: false, dirty: false });
      })
      .catch(() => this.setData({ saving: false, error: '保存失败，请重试' }));
  },
});
