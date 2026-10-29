<template>
  <div class="knowledge-base">
    <!-- 顶部标题与操作 -->
    <div class="page-header">
      <h1>产品知识库</h1>
      <div class="page-subtitle">按产品隔离的知识库条目，用于 AI 文章生成的素材来源</div>
    </div>

    <el-card class="main-card" style="margin-top: 20px;">
      <!-- 产品管理区域 -->
      <div class="section">
        <div class="section-header">
          <el-icon><Folder /></el-icon>
          <span class="section-title">产品管理</span>
          <el-button type="primary" size="small" @click="openProductDialog">新建产品</el-button>
        </div>
        <el-table :data="products" style="width: 100%;" @selection-change="handleProductSelection">
          <el-table-column type="selection" width="55" />
          <el-table-column prop="name" label="产品名称" />
          <el-table-column prop="description" label="产品描述" show-overflow-tooltip />
          <el-table-column prop="brand_color" label="品牌色" width="120">
            <template #default="scope">
              <div class="color-chip" :style="{ backgroundColor: scope.row.brand_color || '#409EFF' }"></div>
              {{ scope.row.brand_color || '#409EFF' }}
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="创建时间" />
          <el-table-column label="操作" width="220">
            <template #default="scope">
              <el-button size="small" type="primary" @click="openProductDialog(scope.row)">编辑</el-button>
              <el-button size="small" type="danger" @click="deleteProduct(scope.row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-if="products.length === 0" class="empty-tip">暂无产品，请先新建一个产品</div>
      </div>

      <!-- 知识库条目区域 -->
      <div class="section" style="margin-top: 28px;">
        <div class="section-header">
          <el-icon><Document /></el-icon>
          <span class="section-title">知识库条目</span>
          <el-select v-model="selectedProductId" placeholder="选择产品" style="width: 180px;" @change="onProductChange">
            <el-option v-for="p in products" :key="p.id" :label="p.name" :value="p.id" />
          </el-select>
          <el-button type="primary" size="small" :disabled="!selectedProductId" @click="openKbDialog">新建条目</el-button>
          <el-button type="danger" size="small" :disabled="selectedKb.length === 0" @click="batchDeleteKb">批量删除</el-button>
        </div>
        <div v-if="!selectedProductId" class="empty-tip">请在上方选择一个产品，查看其知识库条目</div>
        <el-table v-else-if="knowledgeItems.length > 0" :data="knowledgeItems" style="width: 100%;" @selection-change="handleKbSelection">
          <el-table-column type="selection" width="55" />
          <el-table-column prop="title" label="条目标题" min-width="200" show-overflow-tooltip />
          <el-table-column prop="content" label="正文摘要" min-width="260" show-overflow-tooltip>
            <template #default="scope">
              <div class="content-preview">{{ (scope.row.content || '').slice(0, 120) }}{{ (scope.row.content || '').length > 120 ? '...' : '' }}</div>
            </template>
          </el-table-column>
          <el-table-column prop="tags" label="标签" min-width="140" show-overflow-tooltip />
          <el-table-column prop="file_paths" label="上传资料" min-width="180" show-overflow-tooltip>
            <template #default="scope">
              <div class="file-chip-list">
                <span v-for="f in parseFilePaths(scope.row.file_paths)" :key="f" class="file-chip">{{ f.split('/').pop() }}</span>
                <span v-if="!parseFilePaths(scope.row.file_paths)?.length" class="file-chip-empty">无</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="updated_at" label="更新时间" />
          <el-table-column label="操作" width="260">
            <template #default="scope">
              <el-button size="small" type="primary" @click="openKbDialog(scope.row)">编辑</el-button>
              <el-button size="small" type="success" @click="generateContent(scope.row)">AI 生成发布文案</el-button>
              <el-button size="small" @click="vectorizeItem(scope.row)">向量化</el-button>
              <el-button size="small" type="danger" @click="deleteKbItem(scope.row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
        <div v-else class="empty-tip">该产品下暂无知识库条目</div>
      </div>
    </el-card>

    <!-- 产品编辑对话框 -->
    <el-dialog v-model="productDialogVisible" :title="productEditing ? '编辑产品' : '新建产品'" width="420px">
      <el-form :model="productForm" label-width="100px">
        <el-form-item label="产品名称">
          <el-input v-model="productForm.name" placeholder="例如：河狸云蓝枢" />
        </el-form-item>
        <el-form-item label="产品描述">
          <el-input v-model="productForm.description" type="textarea" :rows="3" placeholder="产品一句话介绍" />
        </el-form-item>
        <el-form-item label="品牌色">
          <el-color-picker v-model="productForm.brand_color" show-alpha="false" />
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="productDialogVisible = false">取消</el-button>
          <el-button type="primary" @click="saveProduct">{{ productEditing ? '保存' : '创建' }}</el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 知识库条目编辑对话框 -->
    <el-dialog v-model="kbDialogVisible" :title="kbEditing ? '编辑条目' : '新建条目'" width="520px">
      <el-form :model="kbForm" label-width="120px">
        <el-form-item label="条目标题">
          <el-input v-model="kbForm.title" placeholder="例如：现场看图痛点" />
        </el-form-item>
        <el-form-item label="正文内容">
          <el-input v-model="kbForm.content" type="textarea" :rows="6" placeholder="记录该产品的核心知识，供 AI 生成文章时引用" />
        </el-form-item>
        <el-form-item label="一线痛点">
          <el-input v-model="kbForm.pain_points" type="textarea" :rows="3" placeholder="用户在现场遇到什么痛点" />
        </el-form-item>
        <el-form-item label="解决方案">
          <el-input v-model="kbForm.solutions" type="textarea" :rows="3" placeholder="该产品如何解决上述痛点" />
        </el-form-item>
        <el-form-item label="标签">
          <el-input v-model="kbForm.tags" placeholder="逗号分隔，例如：看图,工地,效率" />
        </el-form-item>
        <el-form-item label="上传资料">
          <el-upload
            class="kb-upload"
            drag
            multiple
            :auto-upload="false"
            :on-change="handleKbFileChange"
            :on-remove="handleKbFileRemove"
            :file-list="kbFileList"
            :limit="10"
          >
            <el-icon class="el-icon--upload"><Upload /></el-icon>
            <div class="el-upload__text">将文件拖到此处，或<em>点击上传</em></div>
            <template #tip>
              <div class="el-upload__tip">支持图片、PDF、文档等，上传后自动关联到本条目</div>
            </template>
          </el-upload>
          <div v-if="kbFileList.length > 0" class="kb-file-list">
            <div v-for="f in kbFileList" :key="f.uid" class="kb-file-item">
              <span class="kb-file-name">{{ f.name }}</span>
              <el-button size="small" type="danger" @click="handleKbFileRemove(f)" style="float: right;">移除</el-button>
            </div>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="kbDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="kbSaving" @click="saveKbItem">{{ kbSaving ? '保存中' : (kbEditing ? '保存' : '创建') }}</el-button>
        </span>
      </template>
    </el-dialog>

    <!-- AI 生成文案预览对话框 -->
    <el-dialog v-model="generateDialogVisible" title="AI 生成的发布文案" width="700px">
      <div class="generate-preview">
        <el-tabs v-model="activeTab">
          <el-tab-pane label="小红书短图文">
            <div class="preview-block">
              <div class="preview-label">标题 (≤20字)</div>
              <el-input v-model="genXhs.title" readonly />
              <div class="preview-body" v-html="formatXhs(genXhs.body)"></div>
            </div>
          </el-tab-pane>
          <el-tab-pane label="四平台长文">
            <div class="preview-block">
              <div class="preview-label">标题 (≤35字)</div>
              <el-input v-model="genLong.title" readonly />
              <div class="preview-body" v-html="formatLong(genLong.body)"></div>
            </div>
          </el-tab-pane>
        </el-tabs>
        <el-alert :title="complianceMsg" :type="compliancePassed ? 'success' : 'warning'" show-icon closable />
        <div class="platform-meta">
          <span v-for="p in genPlatforms" :key="p.platform" class="platform-tag">
            {{ p.platform }}：{{ p.title }}
          </span>
        </div>
      </div>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="generateDialogVisible = false">关闭</el-button>
          <el-button type="primary" @click="publishGenerated">一键发布至贴吧/平台</el-button>
        </span>
      </template>
    </el-dialog>

    <!-- 向量化状态对话框 -->
    <el-dialog v-model="vectorDialogVisible" title="知识库向量化" width="420px">
      <div class="vector-status">
        <div class="vector-item">
          <span>条目 ID：</span>
          <span class="vector-value">{{ vectorItemId }}</span>
        </div>
        <div class="vector-item">
          <span>状态：</span>
          <span :class="vectorStatus === 'done' ? 'status-done' : 'status-doing'">{{ vectorStatusText }}</span>
        </div>
        <div class="vector-item">
          <span>向量维度：</span>
          <span class="vector-value">1536 (预留)</span>
        </div>
        <div class="vector-tip">向量化后，AI 可基于该条目内容检索相关知识生成更精准的文章。</div>
      </div>
      <template #footer>
        <span class="dialog-footer">
          <el-button @click="vectorDialogVisible = false">关闭</el-button>
        </span>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { Folder, Document, Upload, Edit, Delete, Star, ChatLineSquare } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { productApi, knowledgeBaseApi, kbFileApi } from '@/api/product'

// ==================== 产品状态 ====================
const productDialogVisible = ref(false)
const productEditing = ref(false)
const productForm = reactive({ id: '', name: '', description: '', brand_color: '#409EFF' })
const products = ref([])
const selectedProducts = ref([])

const fetchProducts = async () => {
  try {
    const res = await productApi.getAllProducts()
    products.value = res.data || []
  } catch (e) {
    console.error('获取产品失败', e)
  }
}

const openProductDialog = (row = null) => {
  productEditing.value = !!row
  if (row) {
    productForm.id = row.id
    productForm.name = row.name
    productForm.description = row.description || ''
    productForm.brand_color = row.brand_color || '#409EFF'
  } else {
    productForm.id = ''
    productForm.name = ''
    productForm.description = ''
    productForm.brand_color = '#409EFF'
  }
  productDialogVisible.value = true
}

const saveProduct = async () => {
  if (!productForm.name.trim()) {
    ElMessage.warning('产品名称不能为空')
    return
  }
  try {
    if (productEditing.value) {
      await productApi.updateProduct(productForm.id, productForm)
      ElMessage.success('产品更新成功')
    } else {
      await productApi.createProduct(productForm)
      ElMessage.success('产品创建成功')
    }
    productDialogVisible.value = false
    await fetchProducts()
  } catch (e) {
    console.error('保存产品失败', e)
  }
}

const deleteProduct = async (id) => {
  await ElMessageBox.confirm('确定要删除该产品吗？其下的全部知识库条目也会被删除。', '警告', {
    confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning'
  })
  try {
    await productApi.deleteProduct(id)
    ElMessage.success('产品已删除')
    await fetchProducts()
  } catch (e) {
    console.error('删除产品失败', e)
  }
}

const handleProductSelection = (selection) => {
  selectedProducts.value = selection
}

// ==================== 知识库条目状态 ====================
const selectedProductId = ref(null)
const knowledgeItems = ref([])
const selectedKb = ref([])
const kbDialogVisible = ref(false)
const kbEditing = ref(false)
const kbSaving = ref(false)
const kbForm = reactive({ id: '', product_id: '', title: '', content: '', pain_points: '', solutions: '', tags: '', file_paths: '' })
const kbFileList = ref([])

const fetchKnowledgeItems = async () => {
  if (!selectedProductId.value) return
  try {
    const res = await knowledgeBaseApi.getAllKnowledgeBase(selectedProductId.value)
    knowledgeItems.value = res.data || []
  } catch (e) {
    console.error('获取知识库条目失败', e)
  }
}

const onProductChange = () => {
  selectedKb.value = []
  knowledgeItems.value = []
  if (selectedProductId.value) {
    fetchKnowledgeItems()
  }
}

const openKbDialog = (row = null) => {
  if (!selectedProductId.value) {
    ElMessage.warning('请先选择一个产品')
    return
  }
  kbEditing.value = !!row
  if (row) {
    kbForm.id = row.id
    kbForm.product_id = row.product_id
    kbForm.title = row.title || ''
    kbForm.content = row.content || ''
    kbForm.pain_points = row.pain_points || ''
    kbForm.solutions = row.solutions || ''
    kbForm.tags = row.tags || ''
    kbForm.file_paths = row.file_paths || ''
    kbFileList.value = parseFilePaths(row.file_paths).map((p, i) => ({ uid: `u${i}`, name: p.split('/').pop(), url: p, status: 'finished' }))
  } else {
    kbForm.id = ''
    kbForm.product_id = selectedProductId.value
    kbForm.title = ''
    kbForm.content = ''
    kbForm.pain_points = ''
    kbForm.solutions = ''
    kbForm.tags = ''
    kbForm.file_paths = ''
    kbFileList.value = []
  }
  kbDialogVisible.value = true
}

const parseFilePaths = (file_paths) => {
  if (!file_paths) return []
  if (Array.isArray(file_paths)) return file_paths
  try {
    const parsed = JSON.parse(file_paths)
    return Array.isArray(parsed) ? parsed : [String(file_paths)]
  } catch {
    return String(file_paths).split(',').filter(Boolean).map(s => s.trim())
  }
}

const handleKbFileChange = (file, uploadFileList) => {
  kbFileList.value = uploadFileList
}

const handleKbFileRemove = (file) => {
  kbFileList.value = kbFileList.value.filter(f => f.uid !== file.uid)
}

const saveKbItem = async () => {
  if (!kbForm.title.trim()) {
    ElMessage.warning('条目标题不能为空')
    return
  }
  // 把选中的上传文件路径写入 file_paths
  kbForm.file_paths = JSON.stringify(kbFileList.value.map(f => f.url || f.name))
  kbSaving.value = true
  try {
    if (kbEditing.value) {
      await knowledgeBaseApi.updateKbItem(kbForm.id, kbForm)
      ElMessage.success('条目更新成功')
    } else {
      await knowledgeBaseApi.createKbItem(kbForm)
      ElMessage.success('条目创建成功')
    }
    kbDialogVisible.value = false
    await fetchKnowledgeItems()
  } catch (e) {
    console.error('保存条目失败', e)
  } finally {
    kbSaving.value = false
  }
}

const deleteKbItem = async (id) => {
  await ElMessageBox.confirm('确定要删除该知识库条目吗？', '警告', {
    confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning'
  })
  try {
    await knowledgeBaseApi.deleteKbItem(id)
    ElMessage.success('条目已删除')
    await fetchKnowledgeItems()
  } catch (e) {
    console.error('删除条目失败', e)
  }
}

const handleKbSelection = (selection) => {
  selectedKb.value = selection
}

const batchDeleteKb = async () => {
  if (selectedKb.value.length === 0) {
    ElMessage.warning('请先选择要删除的条目')
    return
  }
  await ElMessageBox.confirm(`确定要删除选中的 ${selectedKb.value.length} 个条目吗？`, '批量删除', {
    confirmButtonText: '确定', cancelButtonText: '取消', type: 'warning'
  })
  try {
    for (const item of selectedKb.value) {
      await knowledgeBaseApi.deleteKbItem(item.id)
    }
    ElMessage.success(`成功删除 ${selectedKb.value.length} 个条目`)
    selectedKb.value = []
    await fetchKnowledgeItems()
  } catch (e) {
    console.error('批量删除失败', e)
  }
}

// ==================== AI 生成文案 ====================
const generateDialogVisible = ref(false)
const activeTab = ref('xhs')
const genXhs = reactive({ title: '', body: '' })
const genLong = reactive({ title: '', body: '' })
const genPlatforms = ref([])
const compliancePassed = ref(true)
const complianceMsg = ref('合规自检通过')

const generateContent = async (item) => {
  try {
    const res = await knowledgeBaseApi.generateContent(item.id) // 需后端补充此接口
    if (res.code !== 200) throw new Error(res.msg)
    const data = res.data
    genXhs.title = data.motherboards.xhs.title
    genXhs.body = data.motherboards.xhs.body
    genLong.title = data.motherboards.long.title
    genLong.body = data.motherboards.long.body
    genPlatforms.value = data.platforms || []
    compliancePassed.value = data.compliance?.passed !== false
    complianceMsg.value = compliancePassed.value ? '合规自检通过，无违规词' : '检测到违规词，请修正后再发布'
    generateDialogVisible.value = true
  } catch (e) {
    // 后端若未接 generateContent 单独接口，则回退：把条目内容直接当文案预览
    genXhs.title = (item.title || '').slice(0, 20)
    genXhs.body = item.content || ''
    genLong.title = (item.title || '').slice(0, 35)
    genLong.body = item.content || ''
    genPlatforms.value = []
    compliancePassed.value = true
    complianceMsg.value = '未启用 AI 生成，将使用条目原文发布'
    generateDialogVisible.value = true
  }
}

const formatXhs = (text) => (text || '').replace(/\n/g, '<br>')
const formatLong = (text) => (text || '').replace(/\n/g, '<br>')

const publishGenerated = async () => {
  if (!selectedProductId.value) {
    ElMessage.warning('请先选择产品')
    return
  }
  // 这里应调用后端 /api/publish-from-kb，实际平台由用户在发布中心选择
  ElMessage.info('文案已生成，请前往「发布中心」选择平台并发布')
  generateDialogVisible.value = false
}

// ==================== 向量化（预留） ====================
const vectorDialogVisible = ref(false)
const vectorItemId = ref('')
const vectorStatus = ref('idle') // idle | doing | done
const vectorStatusText = ref('未开始')

const vectorizeItem = async (item) => {
  vectorItemId.value = item.id
  vectorStatus.value = 'doing'
  vectorStatusText.value = '向量化中...'
  vectorDialogVisible.value = true
  try {
    // 预留：调用后端向量化接口
    // await knowledgeBaseApi.vectorize(item.id)
    await new Promise(r => setTimeout(r, 1500))
    vectorStatus.value = 'done'
    vectorStatusText.value = '向量化完成'
    ElMessage.success('知识库条目向量化完成（预留接口）')
  } catch (e) {
    vectorStatus.value = 'idle'
    vectorStatusText.value = '向量化失败'
    ElMessage.error('向量化失败')
  }
}

onMounted(() => {
  fetchProducts()
})
</script>

<style lang="scss" scoped>
@use '@/styles/variables.scss' as *;

.knowledge-base {
  .page-header {
    margin-bottom: 24px;
    h1 {
      font-size: 22px;
      font-weight: 600;
      color: $text-primary;
      margin: 0 0 6px 0;
    }
    .page-subtitle {
      color: $text-regular;
      font-size: 14px;
    }
  }

  .main-card {
    .section {
      .section-header {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 14px;
        .section-title {
          font-size: 16px;
          font-weight: 600;
          color: $text-primary;
        }
      }
    }
    .empty-tip {
      text-align: center;
      color: #909399;
      padding: 30px 0;
    }
    .color-chip {
      display: inline-block;
      width: 14px;
      height: 14px;
      border-radius: 50%;
      margin-right: 6px;
      vertical-align: middle;
      border: 1px solid #fff;
      box-shadow: 0 1px 2px rgba(0,0,0,0.1);
    }
    .content-preview {
      color: #606266;
      font-size: 13px;
      line-height: 1.6;
    }
    .file-chip-list {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }
    .file-chip {
      display: inline-block;
      padding: 4px 10px;
      background: #f0f7ff;
      color: #409eff;
      border: 1px solid #b3e0ff;
      border-radius: 12px;
      font-size: 12px;
      overflow: hidden;
      text-overflow: ellipsis;
      max-width: 160px;
      white-space: nowrap;
    }
    .file-chip-empty {
      color: #909399;
      font-size: 12px;
    }
  }

  .kb-upload {
    width: 100%;
    margin-top: 8px;
  }

  .kb-file-list {
    margin-top: 10px;
  }
  .kb-file-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px;
    border: 1px solid #dcdfe6;
    border-radius: 4px;
    margin-bottom: 6px;
    background: #fafafa;
  }
  .kb-file-name {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    max-width: 260px;
    color: #606266;
  }

  .generate-preview {
    .preview-block {
      margin-bottom: 20px;
      padding: 16px;
      border: 1px solid #e6e7eb;
      border-radius: 6px;
      background: #fafafa;
      .preview-label {
        font-size: 13px;
        color: #909399;
        margin-bottom: 6px;
        font-weight: 600;
      }
      .preview-body {
        font-size: 14px;
        line-height: 1.8;
        color: #303133;
        white-space: pre-wrap;
        word-break: break-word;
      }
    }
    .platform-meta {
      margin-top: 16px;
      padding: 12px;
      background: #f0f7ff;
      border-radius: 6px;
      font-size: 13px;
      color: #1f6ee8;
      .platform-tag {
        display: inline-block;
        margin-right: 12px;
        margin-bottom: 6px;
      }
    }
  }

  .vector-status {
    padding: 20px 0;
    .vector-item {
      display: flex;
      justify-content: space-between;
      margin-bottom: 12px;
      font-size: 14px;
      .vector-value {
        color: #606266;
      }
    }
    .status-doing { color: #e6a23c; }
    .status-done { color: #67c23a; }
    .vector-tip {
      margin-top: 16px;
      font-size: 13px;
      color: #909399;
      padding: 12px;
      background: #fdf6ec;
      border-radius: 6px;
      border-left: 3px solid #e6a23c;
    }
  }

  .dialog-footer {
    display: flex;
    justify-content: flex-end;
    gap: 10px;
  }
}
</style>
