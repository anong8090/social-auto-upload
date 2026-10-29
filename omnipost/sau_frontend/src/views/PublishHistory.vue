<template>
  <div class="publish-history">
    <div class="page-header">
      <div class="header-left">
        <h1>{{ activeTab === 'drafts' ? '草稿箱' : '发布历史' }}</h1>
        <p class="subtitle">
          {{ activeTab === 'drafts' ? '查看并管理已推送至各平台后台的草稿，在平台手动发布后可点击「确认完结」归档至发布记录' : '查看所有通过 Web 界面或 AI 智能体提交的内容分发记录与执行状态' }}
        </p>
      </div>

      <div class="header-right-nav">
        <el-radio-group v-model="activeTab" size="default">
          <el-radio-button label="history">
            <el-icon><List /></el-icon> 发布记录 ({{ total }})
          </el-radio-button>
          <el-radio-button label="drafts">
            <el-icon><FolderOpened /></el-icon> 草稿箱 ({{ draftList.length }})
          </el-radio-button>
        </el-radio-group>
      </div>
    </div>

    <!-- 1. 发布记录视图 -->
    <template v-if="activeTab === 'history'">
      <!-- 筛选工具栏 -->
      <el-card class="filter-card">
        <div class="toolbar">
          <div class="left-filters">
            <el-select v-model="filterPlatform" placeholder="全部平台" clearable style="width: 130px" @change="loadHistory">
              <el-option label="全部平台" :value="null" />
              <el-option v-for="(name, type) in platformMap" :key="type" :label="name" :value="Number(type)" />
            </el-select>

            <el-select v-model="filterStatus" placeholder="全部状态" clearable style="width: 130px" @change="loadHistory">
              <el-option label="全部状态" value="" />
              <el-option label="发布成功" value="success" />
              <el-option label="发布中" value="publishing" />
              <el-option label="发布失败" value="failed" />
              <el-option label="仅预览" value="preview" />
            </el-select>

            <el-input
              v-model="searchKeyword"
              placeholder="搜索标题或内容..."
              clearable
              style="width: 240px"
              @keyup.enter="loadHistory"
              @clear="loadHistory"
            >
              <template #prefix>
                <el-icon><Search /></el-icon>
              </template>
            </el-input>

            <el-button type="primary" @click="loadHistory">查询</el-button>
            <el-button @click="resetFilters">重置</el-button>
          </div>

          <div class="right-actions">
            <el-button :icon="Refresh" circle @click="loadHistory" :loading="loading" title="刷新列表" />
          </div>
        </div>
      </el-card>

      <!-- 历史记录表格 -->
      <el-card class="table-card">
        <el-table
          v-loading="loading"
          :data="historyList"
          stripe
          style="width: 100%"
          empty-text="暂无发布记录"
        >
          <el-table-column prop="id" label="ID" width="65" align="center" />
          
          <el-table-column label="平台" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="getPlatformTagType(row.platform_type)" effect="plain">
                {{ row.platform_name || platformMap[row.platform_type] || ('平台' + row.platform_type) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="account_name" label="账号" width="120" show-overflow-tooltip />

          <el-table-column label="形式" width="85" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.content_type === 'article' || row.content_type === 'note' ? 'warning' : 'info'">
                {{ (row.content_type === 'article' || row.content_type === 'note') ? '图文' : '视频' }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="title" label="标题" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="title-text" @click="showDetail(row)">{{ row.title || '（无标题）' }}</span>
            </template>
          </el-table-column>

          <el-table-column label="附件" width="100" align="center">
            <template #default="{ row }">
              <div v-if="row.files && row.files.length" class="files-preview">
                <el-image
                  v-if="isImageFile(row.files[0])"
                  :src="getImageUrl(row.files[0])"
                  :preview-src-list="getImagePreviewList(row.files)"
                  preview-teleported
                  fit="cover"
                  class="thumb-img"
                />
                <span v-else class="file-count">📎 {{ row.files.length }}个文件</span>
              </div>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>

          <el-table-column label="状态" width="120" align="center">
            <template #default="{ row }">
              <el-tag :type="getStatusTag(row.status)">
                {{ getStatusLabel(row.status) }}
              </el-tag>
              <el-tooltip v-if="row.error_msg" :content="row.error_msg" placement="top">
                <el-icon class="error-tip-icon"><Warning /></el-icon>
              </el-tooltip>
            </template>
          </el-table-column>

          <el-table-column prop="created_at" label="提交时间" width="170" align="center" />

          <el-table-column label="操作" width="270" align="center" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button
                  v-if="row.status === 'failed'"
                  link
                  type="warning"
                  size="small"
                  :icon="RefreshRight"
                  :loading="retryingId === row.id"
                  @click="handleRetry(row)"
                >
                  重试
                </el-button>
                <el-button
                  v-if="row.status === 'failed'"
                  link
                  type="primary"
                  size="small"
                  :icon="Edit"
                  @click="loadIntoPublishCenter(row)"
                >
                  编辑重发
                </el-button>
                <el-button link type="primary" size="small" @click="showDetail(row)">详情</el-button>
                <el-popconfirm title="确定要删除这条发布记录吗？" @confirm="handleDelete(row.id)">
                  <template #reference>
                    <el-button link type="danger" size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </template>
          </el-table-column>
        </el-table>

        <!-- 分页组件 -->
        <div class="pagination-wrapper">
          <el-pagination
            v-model:current-page="currentPage"
            v-model:page-size="pageSize"
            :page-sizes="[10, 20, 50, 100]"
            :total="total"
            layout="total, sizes, prev, pager, next, jumper"
            @size-change="loadHistory"
            @current-change="loadHistory"
          />
        </div>
      </el-card>
    </template>

    <!-- 2. 草稿箱视图 -->
    <template v-else>
      <el-card class="table-card">
        <div class="drafts-toolbar">
          <div class="draft-tip-text">
            <span>📝 当前草稿箱共有 <strong>{{ draftList.length }}</strong> 篇待发布草稿（已勾选 <strong>{{ selectedDraftIds.length }}</strong> 篇）。在平台后台手动发布后，可点击「确认完结」归档至发布记录。</span>
          </div>
          <div class="draft-toolbar-actions">
            <el-button
              type="success"
              :icon="CircleCheck"
              :disabled="!selectedDraftIds.length"
              :loading="confirmingDrafts"
              @click="handleConfirmDrafts(selectedDraftIds)"
            >
              批量确认完结 ({{ selectedDraftIds.length }})
            </el-button>
            <el-popconfirm
              :title="`确定要删除已勾选的 ${selectedDraftIds.length} 篇草稿吗？`"
              @confirm="handleBatchDeleteDrafts"
            >
              <template #reference>
                <el-button
                  type="danger"
                  plain
                  :icon="Delete"
                  :disabled="!selectedDraftIds.length"
                >
                  批量删除 ({{ selectedDraftIds.length }})
                </el-button>
              </template>
            </el-popconfirm>
            <el-button :icon="Refresh" circle @click="loadDrafts" :loading="loadingDrafts" title="刷新草稿列表" />
          </div>
        </div>

        <el-table
          v-loading="loadingDrafts"
          :data="draftList"
          stripe
          style="width: 100%"
          empty-text="草稿箱暂无待发布作品"
          @selection-change="handleDraftSelectionChange"
        >
          <el-table-column type="selection" width="50" align="center" />
          <el-table-column prop="id" label="ID" width="65" align="center" />

          <el-table-column label="平台" width="100" align="center">
            <template #default="{ row }">
              <el-tag :type="getPlatformTagType(row.platform_type)" effect="plain">
                {{ row.platform_name || platformMap[row.platform_type] || ('平台' + row.platform_type) }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column label="形式" width="85" align="center">
            <template #default="{ row }">
              <el-tag size="small" :type="row.content_type === 'article' || row.content_type === 'note' ? 'warning' : 'info'">
                {{ (row.content_type === 'article' || row.content_type === 'note') ? '图文' : '视频' }}
              </el-tag>
            </template>
          </el-table-column>

          <el-table-column prop="title" label="草稿标题" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="title-text" @click="showDraftDetail(row)">{{ row.title || '（未命名草稿）' }}</span>
            </template>
          </el-table-column>

          <el-table-column label="配图" width="100" align="center">
            <template #default="{ row }">
              <div v-if="row.files && row.files.length" class="files-preview">
                <el-image
                  v-if="isImageFile(row.files[0])"
                  :src="getImageUrl(row.files[0])"
                  :preview-src-list="getImagePreviewList(row.files)"
                  preview-teleported
                  fit="cover"
                  class="thumb-img"
                />
                <span v-else class="file-count">📎 {{ row.files.length }}个文件</span>
              </div>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>

          <el-table-column label="话题标签" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">
              <template v-if="row.tags && row.tags.length">
                <el-tag v-for="tag in row.tags.slice(0, 3)" :key="tag" size="small" style="margin-right: 4px;">
                  #{{ tag }}
                </el-tag>
                <span v-if="row.tags.length > 3" class="text-muted">+{{ row.tags.length - 3 }}</span>
              </template>
              <span v-else class="text-muted">-</span>
            </template>
          </el-table-column>

          <el-table-column prop="updated_at" label="最后保存时间" width="170" align="center" />

          <el-table-column label="操作" width="220" align="center" fixed="right">
            <template #default="{ row }">
              <div class="row-actions">
                <el-button
                  type="success"
                  size="small"
                  plain
                  :icon="CircleCheck"
                  :loading="confirmingDrafts"
                  @click="handleConfirmDrafts([row.id])"
                >
                  确认完结
                </el-button>
                <el-button link type="primary" size="small" @click="showDraftDetail(row)">详情</el-button>
                <el-popconfirm title="确定要删除此草稿吗？" @confirm="handleDeleteDraft(row.id)">
                  <template #reference>
                    <el-button link type="danger" size="small">删除</el-button>
                  </template>
                </el-popconfirm>
              </div>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </template>

    <!-- 详情抽屉 -->
    <el-drawer
      v-model="detailVisible"
      title="发布记录详情"
      size="500px"
      direction="rtl"
    >
      <div v-if="currentDetail" class="detail-container">
        <el-descriptions :column="1" border>
          <el-descriptions-item label="记录 ID">{{ currentDetail.id }}</el-descriptions-item>
          <el-descriptions-item label="发布平台">
            <el-tag :type="getPlatformTagType(currentDetail.platform_type)" size="small">
              {{ currentDetail.platform_name }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="发布账号">{{ currentDetail.account_name }}</el-descriptions-item>
          <el-descriptions-item label="内容形式">
            {{ (currentDetail.content_type === 'article' || currentDetail.content_type === 'note') ? '图文内容' : '视频' }}
          </el-descriptions-item>
          <el-descriptions-item label="发布状态">
            <el-tag :type="getStatusTag(currentDetail.status)">
              {{ getStatusLabel(currentDetail.status) }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item v-if="currentDetail.error_msg" label="错误信息">
            <span class="error-text">{{ currentDetail.error_msg }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="提交时间">{{ currentDetail.created_at }}</el-descriptions-item>
          <el-descriptions-item v-if="currentDetail.published_at" label="完成时间">
            {{ currentDetail.published_at }}
          </el-descriptions-item>
        </el-descriptions>

        <div class="content-detail-section">
          <h4>标题</h4>
          <div class="detail-box title-box">{{ currentDetail.title || '（无标题）' }}</div>

          <h4 v-if="currentDetail.content">正文内容</h4>
          <div v-if="currentDetail.content" class="detail-box body-box">{{ currentDetail.content }}</div>

          <h4 v-if="currentDetail.tags && currentDetail.tags.length">话题标签</h4>
          <div v-if="currentDetail.tags && currentDetail.tags.length" class="tags-group">
            <el-tag v-for="tag in currentDetail.tags" :key="tag" size="small" class="tag-item">#{{ tag }}</el-tag>
          </div>

          <h4 v-if="currentDetail.files && currentDetail.files.length">包含素材 ({{ currentDetail.files.length }})</h4>
          <div v-if="currentDetail.files && currentDetail.files.length" class="files-grid">
            <div v-for="(file, idx) in currentDetail.files" :key="idx" class="file-item">
              <el-image
                v-if="isImageFile(file)"
                :src="getImageUrl(file)"
                :preview-src-list="getImagePreviewList(currentDetail.files)"
                preview-teleported
                fit="cover"
                class="grid-img"
              />
              <div v-else class="video-file-tag">🎥 {{ getFileName(file) }}</div>
            </div>
          </div>
        </div>

        <!-- 失败记录或草稿快捷操作底栏 -->
        <div v-if="currentDetail.is_draft" class="drawer-action-footer">
          <el-button
            type="success"
            :icon="CircleCheck"
            :loading="confirmingDrafts"
            @click="handleConfirmDrafts([currentDetail.id], true)"
          >
            确认完结（转入发布记录）
          </el-button>
        </div>
        <div v-else-if="currentDetail.status === 'failed'" class="drawer-action-footer">
          <el-button
            type="warning"
            :icon="RefreshRight"
            :loading="retryingId === currentDetail.id"
            @click="handleRetry(currentDetail)"
          >
            立即重试发布
          </el-button>
          <el-button
            type="primary"
            plain
            :icon="Edit"
            @click="loadIntoPublishCenter(currentDetail)"
          >
            载入到发布中心修改
          </el-button>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Refresh, Warning, RefreshRight, Edit, List, FolderOpened, CircleCheck, Delete } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { http } from '@/utils/request'

const router = useRouter()
const retryingId = ref(null)

const activeTab = ref('history')
const draftList = ref([])
const loadingDrafts = ref(false)
const selectedDraftIds = ref([])
const confirmingDrafts = ref(false)

const loading = ref(false)
const historyList = ref([])
const total = ref(0)
const currentPage = ref(1)
const pageSize = ref(20)
const filterPlatform = ref(null)
const filterStatus = ref('')
const searchKeyword = ref('')

const detailVisible = ref(false)
const currentDetail = ref(null)

const platformMap = {
  1: '小红书',
  2: '视频号',
  3: '抖音',
  4: '快手',
  5: '百家号',
  6: 'B站',
  7: '今日头条',
  8: '搜狐',
  9: '知乎'
}

const getPlatformTagType = (type) => {
  const map = {
    1: 'danger',   // 小红书 红色
    2: 'success',  // 视频号 绿色
    3: '',         // 抖音
    4: 'warning',  // 快手 橙色
    5: 'primary',  // 百家号 蓝色
    6: 'warning',  // B站
    7: 'danger',   // 头条
    8: 'info',     // 搜狐
    9: 'primary',  // 知乎
  }
  return map[type] || 'info'
}

const getStatusTag = (status) => {
  switch (status) {
    case 'success': return 'success'
    case 'draft': return 'success'
    case 'failed': return 'danger'
    case 'publishing': return 'primary'
    case 'preview': return 'warning'
    default: return 'info'
  }
}

const getStatusLabel = (status) => {
  switch (status) {
    case 'success': return '发布成功'
    case 'draft': return '已存草稿'
    case 'failed': return '发布失败'
    case 'publishing': return '发布中...'
    case 'preview': return '仅预览'
    default: return '排队中'
  }
}

const extractFilePath = (file) => {
  if (!file) return ''
  if (typeof file === 'string') return file
  return file.filename || file.path || file.name || ''
}

const isImageFile = (file) => {
  const pathStr = extractFilePath(file)
  if (!pathStr) return false
  const lower = String(pathStr).toLowerCase()
  return lower.endsWith('.png') || lower.endsWith('.jpg') || lower.endsWith('.jpeg') || lower.endsWith('.webp') || lower.endsWith('.gif')
}

const getFileName = (path) => {
  const pathStr = extractFilePath(path)
  if (!pathStr) return ''
  return String(pathStr).split('/').pop().split('\\').pop()
}

const getImageUrl = (path) => {
  const filename = getFileName(path)
  if (!filename) return ''
  return `/api/getFile?filename=${encodeURIComponent(filename)}`
}

const getImagePreviewList = (files) => {
  if (!files || !files.length) return []
  return files.filter(f => isImageFile(f)).map(f => getImageUrl(f))
}

let pollTimer = null

const checkAndStartPoll = () => {
  const hasPublishing = historyList.value.some(item => item.status === 'publishing')
  if (hasPublishing && !pollTimer) {
    pollTimer = setInterval(async () => {
      await loadHistory(false)
      const stillPublishing = historyList.value.some(item => item.status === 'publishing')
      if (!stillPublishing && pollTimer) {
        clearInterval(pollTimer)
        pollTimer = null
      }
    }, 3000)
  } else if (!hasPublishing && pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

const loadHistory = async (showLoading = true) => {
  if (showLoading) loading.value = true
  try {
    const params = {
      page: currentPage.value,
      pageSize: pageSize.value,
      platform: filterPlatform.value,
      status: filterStatus.value || undefined,
      keyword: searchKeyword.value || undefined
    }
    const res = await http.get('/getPublishHistory', params)
    if (res.code === 200 && res.data) {
      historyList.value = res.data.list || []
      total.value = res.data.total || 0
      checkAndStartPoll()
    }
  } catch (err) {
    console.error('加载发布历史异常:', err)
  } finally {
    if (showLoading) loading.value = false
  }
}

const resetFilters = () => {
  filterPlatform.value = null
  filterStatus.value = ''
  searchKeyword.value = ''
  currentPage.value = 1
  loadHistory()
}

const showDetail = (row) => {
  currentDetail.value = row
  detailVisible.value = true
}

const handleDelete = async (id) => {
  try {
    const res = await http.post('/deletePublishHistory', { id })
    if (res.code === 200) {
      ElMessage.success('删除成功')
      loadHistory()
    } else {
      ElMessage.error(res.msg || '删除失败')
    }
  } catch (err) {
    ElMessage.error('删除异常')
  }
}

const handleRetry = async (row) => {
  if (!row || !row.id) return
  retryingId.value = row.id
  try {
    const res = await http.post('/retryPublish', { id: row.id })
    if (res && res.code === 200) {
      ElMessage.success('已发起重试，后台正在自动执行...')
      row.status = 'publishing'
      row.error_msg = null
      if (currentDetail.value && currentDetail.value.id === row.id) {
        currentDetail.value.status = 'publishing'
        currentDetail.value.error_msg = null
      }
      checkAndStartPoll()
    } else {
      ElMessage.error(res?.msg || '发起重试失败')
    }
  } catch (err) {
    console.error('发起重试异常:', err)
    ElMessage.error(err?.response?.data?.msg || '发起重试失败')
  } finally {
    retryingId.value = null
  }
}

const loadIntoPublishCenter = (row) => {
  if (!row) return
  try {
    sessionStorage.setItem('retry_publish_data', JSON.stringify({
      platform_type: row.platform_type,
      content_type: row.content_type || 'article',
      title: row.title || '',
      content: row.content || '',
      tags: row.tags || [],
      files: row.files || []
    }))
    ElMessage.info('正在载入到发布中心...')
    router.push('/publish-center')
  } catch (e) {
    console.error(e)
    ElMessage.error('载入发布中心失败')
  }
}

// ----------------- 草稿箱相关逻辑 -----------------
const handleDraftSelectionChange = (selection) => {
  selectedDraftIds.value = (selection || []).map(item => item.id)
}

const loadDrafts = async (showLoading = true) => {
  if (showLoading) loadingDrafts.value = true
  try {
    const res = await http.get('/getDrafts')
    if (res && res.code === 200) {
      draftList.value = res.data || []
      selectedDraftIds.value = []
    }
  } catch (err) {
    console.error('加载草稿列表异常:', err)
  } finally {
    if (showLoading) loadingDrafts.value = false
  }
}

const showDraftDetail = (row) => {
  currentDetail.value = {
    ...row,
    content: row.body || row.content,
    is_draft: true
  }
  detailVisible.value = true
}

const handleConfirmDrafts = async (ids, closeDrawer = false) => {
  const validIds = (ids || []).filter(Boolean)
  if (!validIds.length) return
  confirmingDrafts.value = true
  try {
    const res = await http.post('/confirmDrafts', { ids: validIds })
    if (res && res.code === 200) {
      ElMessage.success(res.msg || `已确认完结 ${validIds.length} 篇草稿并归档至发布记录`)
      if (closeDrawer) detailVisible.value = false
      await Promise.all([loadDrafts(false), loadHistory(false)])
    } else {
      ElMessage.error(res?.msg || '确认完结失败')
    }
  } catch (e) {
    console.error('确认草稿完结异常:', e)
    ElMessage.error('确认完结失败')
  } finally {
    confirmingDrafts.value = false
  }
}

const handleDeleteDraft = async (id) => {
  try {
    const res = await http.post('/deleteDraft', { id })
    if (res && res.code === 200) {
      ElMessage.success('草稿已删除')
      loadDrafts(false)
    } else {
      ElMessage.error(res?.msg || '删除草稿失败')
    }
  } catch (e) {
    console.error('删除草稿异常:', e)
    ElMessage.error('删除草稿失败')
  }
}

const handleBatchDeleteDrafts = async () => {
  if (!selectedDraftIds.value.length) return
  try {
    const res = await http.post('/deleteDraft', { ids: selectedDraftIds.value })
    if (res && res.code === 200) {
      ElMessage.success(res.msg || '批量删除草稿成功')
      loadDrafts(false)
    } else {
      ElMessage.error(res?.msg || '批量删除草稿失败')
    }
  } catch (e) {
    console.error('批量删除草稿异常:', e)
    ElMessage.error('批量删除草稿失败')
  }
}

onMounted(() => {
  loadHistory()
  loadDrafts()
})

onUnmounted(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<style scoped lang="scss">
.publish-history {
  padding: 20px;

  .page-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 20px;

    .header-left {
      h1 {
        margin: 0 0 8px 0;
        font-size: 24px;
        font-weight: 600;
        color: #303133;
      }

      .subtitle {
        margin: 0;
        font-size: 14px;
        color: #909399;
      }
    }
  }

  .drafts-toolbar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    padding-bottom: 12px;
    border-bottom: 1px dashed #ebeef5;
    gap: 12px;
    flex-wrap: wrap;

    .draft-tip-text {
      font-size: 14px;
      color: #606266;
      strong {
        color: #e6a23c;
        font-size: 15px;
      }
    }

    .draft-toolbar-actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
  }

  .filter-card {
    margin-bottom: 20px;

    .toolbar {
      display: flex;
      justify-content: space-between;
      align-items: center;

      .left-filters {
        display: flex;
        gap: 12px;
        align-items: center;
        flex-wrap: wrap;
      }
    }
  }

  .table-card {
    .title-text {
      color: #409EFF;
      cursor: pointer;
      font-weight: 500;
      &:hover {
        text-decoration: underline;
      }
    }

    .thumb-img {
      width: 48px;
      height: 48px;
      border-radius: 4px;
      border: 1px solid #EBEEF5;
      display: block;
    }

    .file-count {
      font-size: 13px;
      color: #606266;
    }

    .error-tip-icon {
      color: #F56C6C;
      margin-left: 4px;
      vertical-align: middle;
      cursor: pointer;
    }

    .row-actions {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      white-space: nowrap;

      .el-button {
        margin: 0;
        padding: 0 4px;
        font-size: 13px;
      }
    }

    .pagination-wrapper {
      margin-top: 20px;
      display: flex;
      justify-content: flex-end;
    }
  }

  .detail-container {
    .content-detail-section {
      margin-top: 20px;

      h4 {
        margin: 14px 0 6px 0;
        font-size: 15px;
        color: #303133;
      }

      .detail-box {
        background: #F8F9FA;
        border-radius: 6px;
        padding: 12px;
        border: 1px solid #E4E7ED;
        font-size: 14px;
        line-height: 1.6;
        color: #303133;
        white-space: pre-wrap;
        word-break: break-all;
      }

      .title-box {
        font-weight: 600;
      }

      .tags-group {
        display: flex;
        gap: 8px;
        flex-wrap: wrap;
      }

      .files-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;
        margin-top: 8px;

        .grid-img {
          width: 100%;
          height: 110px;
          border-radius: 4px;
          border: 1px solid #DCDFE6;
        }

        .video-file-tag {
          font-size: 12px;
          color: #606266;
          background: #f4f4f5;
          padding: 8px;
          border-radius: 4px;
          grid-column: span 3;
          word-break: break-all;
        }
      }
    }

    .drawer-action-footer {
      display: flex;
      justify-content: flex-end;
      gap: 12px;
      margin-top: 24px;
      padding-top: 16px;
      border-top: 1px solid #ebeef5;
    }

    .error-text {
      color: #F56C6C;
      font-size: 13px;
    }
  }
}
</style>
