// 兴趣: feed topics — the interests that seed the daily story.
const api = require('../../utils/api');

// Curated topics the CN sources can actually serve: fetcher_cn matches
// interests as substrings of 百度/微博 hot-list titles, so these are the
// short topical words that actually appear there.
const SUGGESTED = [
  '人工智能', '科技', '编程', '手机', '游戏', '电影',
  '音乐', '体育', '足球', '财经', '股市', '健康',
  '教育', '旅行', '美食', '汽车', '航天', '国际',
];

Page({
  data: {
    interests: [],
    suggested: [],
    language: '',
    draft: '',
    dirty: false,
    saving: false,
    error: '',
  },

  updateSuggested(interests) {
    return SUGGESTED.filter(t => !interests.includes(t));
  },

  onShow() {
    api.get('/api/config')
      .then(cfg => this.setData({
        interests: cfg.interests || [],
        suggested: this.updateSuggested(cfg.interests || []),
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
    const interests = [...this.data.interests, topic];
    this.setData({
      interests,
      suggested: this.updateSuggested(interests),
      draft: '',
      dirty: true,
    });
  },

  addSuggested(e) {
    const topic = e.currentTarget.dataset.topic;
    if (this.data.interests.includes(topic)) return;
    const interests = [...this.data.interests, topic];
    this.setData({ interests, suggested: this.updateSuggested(interests), dirty: true });
  },

  removeInterest(e) {
    const interests = this.data.interests.slice();
    interests.splice(e.currentTarget.dataset.index, 1);
    this.setData({ interests, suggested: this.updateSuggested(interests), dirty: true });
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
