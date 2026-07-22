// 故事: today's story — the daily reading loop (show-me / keep-reading / love).
const api = require('../../utils/api');

Page({
  data: {
    story: null,
    lovedList: [],
    expandedLoved: '',
    loved: false,
    busy: false,
    error: '',
  },

  onShow() {
    this.loadLoved();
  },

  loadLoved() {
    api.get('/api/loved')
      .then(lovedList => this.setData({ lovedList: lovedList.reverse() }))
      .catch(() => {});
  },

  showMe() {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    api.post('/api/show-me', {})
      .then(story => {
        if (story.error) {
          this.setData({ error: story.error, busy: false });
          return;
        }
        this.setData({ story, loved: false, busy: false });
      })
      .catch(() => this.setData({ busy: false, error: '故事生成失败，请重试' }));
  },

  keepReading() {
    if (this.data.busy) return;
    this.setData({ busy: true, error: '' });
    api.post('/api/keep-reading', {})
      .then(story => {
        if (story.error) {
          this.setData({ error: story.error, busy: false });
          return;
        }
        this.setData({ story, busy: false });
      })
      .catch(() => this.setData({ busy: false, error: '续写失败，请重试' }));
  },

  love() {
    api.post('/api/love', {})
      .then(saved => {
        if (saved.error) {
          this.setData({ error: saved.error });
          return;
        }
        this.setData({ loved: true });
        this.loadLoved();
      })
      .catch(() => this.setData({ error: '收藏失败，请重试' }));
  },

  toggleLoved(e) {
    const id = e.currentTarget.dataset.id;
    this.setData({ expandedLoved: this.data.expandedLoved === id ? '' : id });
  },

  confirmUnlove(e) {
    const story = this.data.lovedList[e.currentTarget.dataset.index];
    wx.showModal({
      title: '取消收藏',
      content: `不再收藏「${story.original.title || story.loved_at}」？`,
      confirmText: '移除',
      confirmColor: '#dc2626',
      success: res => {
        if (!res.confirm) return;
        api.post('/api/unlove', { id: story.id }).then(() => this.loadLoved());
      },
    });
  },
});
