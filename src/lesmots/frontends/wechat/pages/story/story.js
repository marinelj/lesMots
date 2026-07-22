// 故事: today's story — the daily reading loop (show-me / keep-reading / love).
const api = require('../../utils/api');

// Split the LLM's "**word**" markup into render segments; never let raw
// asterisks reach the screen. fam: 1-5 from the bank, 0 = word not found.
function parseStory(text, famMap) {
  const segments = [];
  const re = /\*\*([^*]+)\*\*/g;
  let last = 0;
  let m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) {
      segments.push({ t: text.slice(last, m.index).replace(/\*+/g, ''), hl: false, fam: 0 });
    }
    segments.push({ t: m[1], hl: true, fam: famMap[m[1].trim().toLowerCase()] || 0 });
    last = re.lastIndex;
  }
  if (last < text.length) {
    segments.push({ t: text.slice(last).replace(/\*+/g, ''), hl: false, fam: 0 });
  }
  return segments;
}

function plain(text) {
  return (text || '').replace(/\*+/g, '');
}

Page({
  data: {
    story: null,
    segments: [],
    lovedList: [],
    expandedLoved: '',
    loved: false,
    busy: false,
    error: '',
  },

  famMap: {},

  onShow() {
    this.loadFamMap().then(() => {
      // re-parse anything already on screen with fresh familiarity levels
      if (this.data.story) {
        this.setData({ segments: parseStory(this.data.story.rewritten, this.famMap) });
      }
      this.loadLoved();
    });
  },

  onWordAdded() {
    // a new deposit may appear in the current story — recolor it
    this.loadFamMap().then(() => {
      if (this.data.story) {
        this.setData({ segments: parseStory(this.data.story.rewritten, this.famMap) });
      }
    });
  },

  loadFamMap() {
    return api.get('/api/words')
      .then(words => {
        this.famMap = {};
        words.forEach(w => {
          this.famMap[w.text.trim().toLowerCase()] = w.familiarity || 1;
        });
      })
      .catch(() => {});
  },

  loadLoved() {
    return api.get('/api/loved')
      .then(lovedList => this.setData({
        lovedList: lovedList.reverse().map(s => ({
          ...s,
          preview: (s.original && s.original.title) || plain(s.rewritten),
          segments: parseStory(s.rewritten, this.famMap),
        })),
      }))
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
        this.setData({
          story,
          segments: parseStory(story.rewritten, this.famMap),
          loved: false,
          busy: false,
        });
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
        this.setData({
          story,
          segments: parseStory(story.rewritten, this.famMap),
          busy: false,
        });
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
      content: `不再收藏「${story.preview || story.loved_at}」？`,
      confirmText: '移除',
      confirmColor: '#dc2626',
      success: res => {
        if (!res.confirm) return;
        api.post('/api/unlove', { id: story.id }).then(() => this.loadLoved());
      },
    });
  },
});
