<template>
  <div class="publish-studio" @paste="handleGlobalPaste">
    <!-- 顶部导航工作栏 -->
    <header class="studio-header">
      <div class="header-left">
        <div class="editor-status-badge" :class="{ 'is-draft': currentDraftId }">
          <el-icon v-if="currentDraftId"><Document /></el-icon>
          <el-icon v-else><EditPen /></el-icon>
          <span>{{ currentDraftId ? `正在编辑草稿 #${currentDraftId}：${currentDraftTitle || '未命名'}` : '新建作品' }}</span>
          <el-button 
            v-if="currentDraftId" 
            size="small" 
            text 
            type="info" 
            class="exit-draft-btn" 
            @click="handleNewCreation"
          >
            退出草稿
          </el-button>
        </div>
      </div>

      <div class="header-actions">
        <el-button 
          size="default" 
          :icon="Plus" 
          @click="handleNewCreation"
        >
          新建创作
        </el-button>

        <el-button 
          size="default" 
          type="primary" 
          plain 
          :icon="Document" 
          :loading="savingDraft" 
          @click="saveCurrentDraft"
        >
          存为草稿
        </el-button>

        <el-button 
          size="default" 
          type="primary" 
          class="primary-publish-btn" 
          :icon="Promotion" 
          :loading="publishing" 
          @click="confirmPublish"
        >
          {{ publishing ? '发布中...' : '立即发布' }}
        </el-button>
      </div>
    </header>

    <!-- 工作台双栏布局 -->
    <main class="studio-body">
      <!-- 左栏：核心创作与媒体编排 -->
      <section class="content-panel">
        <!-- 状态提示信息 -->
        <el-alert
          v-if="publishStatus"
          :title="publishStatus.message"
          :type="publishStatus.type"
          show-icon
          closable
          @close="publishStatus = null"
          class="status-banner"
        />

        <!-- 标题输入区域 -->
        <div class="form-block title-block">
          <div class="block-label">
            <span>标题</span>
            <span class="char-counter" :class="{ 'text-danger': isTitleOverLimit }">
              {{ form.title.length }} / {{ maxTitleLength }} 字
            </span>
          </div>
          <el-input
            v-model="form.title"
            placeholder="输入作品标题（小红书建议20字以内，醒目吸引人）..."
            maxlength="100"
            size="large"
            class="title-input-field"
            clearable
          />
        </div>

        <!-- 正文内容编辑区域 -->
        <div class="form-block body-block">
          <div class="block-label">
            <span>正文内容</span>
            <span class="char-counter">{{ form.articleBody.length }} 字</span>
          </div>
          <el-input
            v-model="form.articleBody"
            type="textarea"
            :rows="8"
            placeholder="撰写你的正文或文章内容...（支持在编辑区域直接按 Cmd+V / Ctrl+V 粘贴截图）"
            class="body-textarea-field"
          />
        </div>

        <!-- 媒体编排区域：图文模式 -->
        <div v-if="isArticleMode" class="form-block media-block">
          <div class="block-label">
            <span>图片素材（支持 1-{{ maxImageCount }} 张，第 1 张自动设为封面）</span>
            <span class="char-counter">{{ form.fileList.length }} / {{ maxImageCount }} 张</span>
          </div>

          <!-- 全能拖拽与上传投放区 -->
          <div
            class="dropzone-area"
            :class="{ 'is-dragover': isDraggingOver }"
            @dragover.prevent="onDragOver"
            @dragleave.prevent="onDragLeave"
            @drop.prevent="onFileDrop"
          >
            <div class="dropzone-content">
              <el-icon class="dropzone-icon"><UploadFilled /></el-icon>
              <div class="dropzone-text">
                <strong>点击或将图片拖拽至此处</strong>
                <p>支持从本地文件夹拖拽多图、<strong>从浏览器其他网页直接拖图</strong>、或使用 <code>Cmd+V / Ctrl+V</code> 截图粘贴</p>
              </div>
              <div class="dropzone-buttons">
                <el-button type="primary" size="small" @click="triggerFileInput">
                  <el-icon><Upload /></el-icon> 本地选择
                </el-button>
                <el-button type="success" size="small" @click="selectMaterialLibrary">
                  <el-icon><Folder /></el-icon> 素材库导入
                </el-button>
              </div>
            </div>
            <!-- 隐藏的本地文件 input -->
            <input
              ref="fileInputRef"
              type="file"
              accept="image/*"
              multiple
              style="display: none"
              @change="handleFileInputChange"
            />
          </div>

          <!-- 图片卡片网格与调序 -->
          <div v-if="form.fileList.length > 0" class="image-grid-section">
            <div class="grid-tip">
              <span>💡 提示：按住鼠标可<strong>拖拽调换图片顺序</strong>；排列在第一位的图片将作为发布封面。</span>
            </div>
            <div class="image-cards-grid">
              <div
                v-for="(file, index) in form.fileList"
                :key="file.path || file.name || index"
                class="image-card"
                :class="{ 'is-cover': index === 0, 'is-dragging': draggedImageIndex === index }"
                draggable="true"
                @dragstart="onImageDragStart(index, $event)"
                @dragover.prevent="onImageDragOver(index, $event)"
                @drop.prevent="onImageDrop(index, $event)"
                @dragend="onImageDragEnd"
              >
                <!-- 封面徽标 -->
                <div v-if="index === 0" class="cover-tag">封面</div>
                <div v-else class="index-tag">{{ index + 1 }}</div>

                <div class="image-thumb-wrap" @click="previewImage(file.url)">
                  <img :src="file.url" :alt="file.name" class="image-thumb" />
                  <div class="thumb-hover-overlay">
                    <el-icon><ZoomIn /></el-icon>
                  </div>
                </div>

                <div class="image-card-footer">
                  <span class="file-name" :title="file.name">{{ file.name }}</span>
                  <div class="card-action-bar">
                    <el-tooltip content="设为首图封面" placement="top" v-if="index !== 0">
                      <el-button size="small" text type="primary" :icon="Top" @click.stop="setAsCover(index)" />
                    </el-tooltip>
                    <el-tooltip content="左移" placement="top" v-if="index > 0">
                      <el-button size="small" text :icon="ArrowLeft" @click.stop="moveImage(index, -1)" />
                    </el-tooltip>
                    <el-tooltip content="右移" placement="top" v-if="index < form.fileList.length - 1">
                      <el-button size="small" text :icon="ArrowRight" @click.stop="moveImage(index, 1)" />
                    </el-tooltip>
                    <el-tooltip content="删除此图片" placement="top">
                      <el-button size="small" text type="danger" :icon="Delete" @click.stop="removeImage(index)" />
                    </el-tooltip>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 媒体编排区域：视频模式 -->
        <div v-else class="form-block video-block">
          <div class="block-label">
            <span>视频文件</span>
            <span class="char-counter">{{ form.fileList.length > 0 ? '已就绪' : '未上传' }}</span>
          </div>

          <div
            v-if="form.fileList.length === 0"
            class="dropzone-area video-dropzone"
            :class="{ 'is-dragover': isDraggingOver }"
            @dragover.prevent="onDragOver"
            @dragleave.prevent="onDragLeave"
            @drop.prevent="onFileDrop"
          >
            <div class="dropzone-content">
              <el-icon class="dropzone-icon"><VideoCamera /></el-icon>
              <div class="dropzone-text">
                <strong>点击或将视频拖拽至此处</strong>
                <p>支持 MP4、MOV、AVI 等主流视频格式</p>
              </div>
              <div class="dropzone-buttons">
                <el-button type="primary" size="small" @click="triggerVideoInput">
                  <el-icon><Upload /></el-icon> 本地选择视频
                </el-button>
                <el-button type="success" size="small" @click="selectMaterialLibrary">
                  <el-icon><Folder /></el-icon> 素材库导入
                </el-button>
              </div>
            </div>
            <input
              ref="videoInputRef"
              type="file"
              accept="video/*"
              style="display: none"
              @change="handleVideoInputChange"
            />
          </div>

          <!-- 已上传视频卡片 -->
          <div v-else class="video-preview-card">
            <div class="video-info">
              <el-icon class="video-file-icon"><VideoCamera /></el-icon>
              <div class="file-meta">
                <span class="file-name">{{ form.fileList[0].name }}</span>
                <span class="file-size">{{ (form.fileList[0].size / 1024 / 1024).toFixed(2) }} MB</span>
              </div>
            </div>
            <div class="video-actions">
              <el-link :href="form.fileList[0].url" target="_blank" type="primary">播放预览</el-link>
              <el-button type="danger" size="small" text :icon="Delete" @click="form.fileList = []">重新选择</el-button>
            </div>
          </div>
        </div>
      </section>

      <!-- 右栏：发布平台与高级配置面板 -->
      <aside class="settings-panel">
        <el-card class="setting-card" shadow="never">
          <template #header>
            <div class="card-header-title">
              <span>🎯 目标平台</span>
            </div>
          </template>

          <!-- 平台选择网格 -->
          <div class="platform-grid">
            <div
              v-for="platform in platforms"
              :key="platform.key"
              class="platform-item"
              :class="{ 'is-active': form.selectedPlatform === platform.key }"
              @click="switchPlatform(platform.key)"
            >
              <span class="platform-icon">{{ platform.icon }}</span>
              <span class="platform-name">{{ platform.name }}</span>
            </div>
          </div>

          <!-- 内容模式切换（针对支持图文与视频的平台） -->
          <div v-if="articleCapablePlatforms.includes(form.selectedPlatform) && !articleOnlyPlatforms.includes(form.selectedPlatform)" class="sub-setting-row">
            <span class="sub-label">内容类型：</span>
            <el-radio-group v-model="form.contentType" size="small">
              <el-radio-button label="article">{{ form.selectedPlatform === 1 ? '图文笔记' : (form.selectedPlatform === 2 ? '图文动态' : '图文文章') }}</el-radio-button>
              <el-radio-button label="video">短视频</el-radio-button>
            </el-radio-group>
          </div>
        </el-card>

        <!-- 账号绑定 -->
        <el-card class="setting-card" shadow="never">
          <template #header>
            <div class="card-header-title">
              <span>👤 发布账号</span>
              <router-link to="/account-management" class="manage-account-link">账号管理</router-link>
            </div>
          </template>

          <div v-if="availableAccounts.length === 0" class="no-account-tip">
            <p>该平台尚未绑定有效账号</p>
            <el-button size="small" type="primary" plain @click="$router.push('/account-management')">
              前往添加账号
            </el-button>
          </div>
          <div v-else class="account-select-wrap">
            <el-select
              v-model="form.selectedAccounts"
              multiple
              placeholder="选择发布的账号（支持多账号）"
              class="account-selector"
            >
              <el-option
                v-for="acc in availableAccounts"
                :key="acc.id"
                :label="acc.name"
                :value="acc.id"
              />
            </el-select>
          </div>
        </el-card>

        <!-- 话题与标签 -->
        <el-card class="setting-card" shadow="never">
          <template #header>
            <div class="card-header-title">
              <span>🏷️ 话题与标签</span>
            </div>
          </template>

          <div class="topics-input-wrap">
            <el-input
              v-model="newTopicInput"
              placeholder="输入标签按回车添加..."
              size="small"
              @keyup.enter="addTopic"
            >
              <template #append>
                <el-button @click="addTopic">添加</el-button>
              </template>
            </el-input>

            <div class="selected-tags-box" v-if="form.selectedTopics.length > 0">
              <el-tag
                v-for="(tag, idx) in form.selectedTopics"
                :key="tag"
                closable
                size="small"
                @close="removeTopic(idx)"
                class="topic-tag"
              >
                #{{ tag }}
              </el-tag>
            </div>

            <!-- 热门推荐话题快速选用 -->
            <div class="recommended-tags">
              <span class="rec-title">常用标签：</span>
              <div class="rec-list">
                <el-tag
                  v-for="topic in recommendedTopics"
                  :key="topic"
                  size="small"
                  effect="plain"
                  class="rec-tag"
                  @click="toggleTopic(topic)"
                >
                  {{ form.selectedTopics.includes(topic) ? '✓ ' : '+ ' }}{{ topic }}
                </el-tag>
              </div>
            </div>
          </div>
        </el-card>

        <!-- 合规声明与高级选项 -->
        <el-card class="setting-card" shadow="never">
          <template #header>
            <div class="card-header-title">
              <span>⚙️ 发布与合规声明</span>
            </div>
          </template>

          <div class="declarations-list">
            <!-- AI 生成自主声明 -->
            <el-checkbox v-model="form.aiGenerated" class="setting-checkbox">
              标注「内容由 AI 生成」自主声明
            </el-checkbox>

            <!-- 仅预览不发布 -->
            <el-checkbox v-model="form.dryRun" class="setting-checkbox">
              仅预览填表不点击发布（测试对齐用）
            </el-checkbox>

            <!-- 视频号原创 -->
            <el-checkbox v-if="form.selectedPlatform === 2" v-model="form.isOriginal" class="setting-checkbox">
              标记为原创内容（视频号）
            </el-checkbox>

            <!-- 保存为平台官方草稿 -->
            <el-checkbox v-if="![4, 6].includes(form.selectedPlatform)" v-model="form.isDraft" class="setting-checkbox">
              保存至平台官方草稿箱（后续手动发布，防封更安全）
            </el-checkbox>

            <!-- B站创作声明 -->
            <div v-if="form.selectedPlatform === 6" class="sub-setting-block">
              <span class="sub-label">B站创作声明：</span>
              <el-select v-model="form.bilibiliCreationStatement" size="small" class="full-width">
                <el-option
                  v-for="opt in bilibiliCreationOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </div>

            <!-- 今日头条作品声明 -->
            <div v-if="form.selectedPlatform === 7 && isArticleMode" class="sub-setting-block">
              <span class="sub-label">头条作品声明：</span>
              <el-select v-model="form.workStatement" size="small" class="full-width" clearable placeholder="无声明">
                <el-option v-for="stmt in toutiaoWorkStatementOptions" :key="stmt" :label="stmt" :value="stmt" />
              </el-select>
            </div>

            <!-- 百度贴吧目标贴吧指定 -->
            <div v-if="form.selectedPlatform === 10" class="sub-setting-block">
              <span class="sub-label">目标贴吧（二选一即可）：</span>
              <el-input v-model="form.tiebaFid" size="small" placeholder="贴吧 ID（fid，优先）" class="full-width" />
              <el-input v-model="form.tiebaKw" size="small" placeholder="贴吧名（kw，fid 为空时使用）" class="full-width" style="margin-top:8px" />
            </div>
          </div>
        </el-card>
      </aside>
    </main>

    <!-- 草稿箱抽屉 -->
    <el-drawer
      v-model="draftDrawerVisible"
      title="草稿箱"
      size="540px"
      :destroy-on-close="false"
      class="draft-drawer"
    >
      <template #header>
        <div class="drawer-header-custom">
          <div class="drawer-title-group">
            <span class="drawer-title-text">我的草稿 ({{ draftList.length }})</span>
            <span v-if="completedDraftsCount > 0" class="drawer-completed-badge">
              {{ completedDraftsCount }} 篇已完结
            </span>
          </div>
          <el-button size="small" :icon="Refresh" @click="fetchDrafts" circle :loading="loadingDrafts" title="刷新列表" />
        </div>
      </template>

      <div v-loading="loadingDrafts" class="draft-drawer-body">
        <div class="draft-tabs-filter">
          <el-radio-group v-model="draftFilterTab" size="small">
            <el-radio-button label="all">全部 ({{ draftList.length }})</el-radio-button>
            <el-radio-button label="draft">编写中 ({{ pendingDraftsCount }})</el-radio-button>
            <el-radio-button label="completed">已完结 ({{ completedDraftsCount }})</el-radio-button>
          </el-radio-group>
        </div>

        <div v-if="filteredDraftList.length === 0" class="draft-empty-wrap">
          <el-empty :description="draftFilterTab === 'draft' ? '暂无编写中的草稿' : (draftFilterTab === 'completed' ? '暂无已完结作品' : '暂无草稿内容')" :image-size="80" />
        </div>

        <div v-else class="draft-cards-scroll">
          <div
            v-for="draft in filteredDraftList"
            :key="draft.id"
            class="draft-item-card"
            :class="{ 'is-current': currentDraftId === draft.id, 'is-completed': draft.status === 'completed' }"
          >
            <div class="card-head">
              <div class="head-tags">
                <el-tag size="small" effect="light" type="primary">
                  {{ draft.platform_name || getPlatformName(draft.platform_type) }}
                </el-tag>
                <el-tag size="small" effect="plain" :type="draft.content_type === 'article' ? 'success' : 'warning'" style="margin-left: 6px;">
                  {{ draft.content_type === 'article' ? '图文' : '视频' }}
                </el-tag>
                <el-tag 
                  v-if="draft.status === 'completed'" 
                  size="small" 
                  effect="dark" 
                  type="success" 
                  style="margin-left: 6px;"
                >
                  ✅ 已完结
                </el-tag>
                <el-tag 
                  v-else 
                  size="small" 
                  effect="plain" 
                  type="warning" 
                  style="margin-left: 6px;"
                >
                  📝 编写中
                </el-tag>
              </div>
              <span class="card-date">{{ draft.updated_at }}</span>
            </div>

            <!-- 完结同步说明条 -->
            <div v-if="draft.status === 'completed' && (draft.completion_note || draft.synced_platforms)" class="card-completion-banner">
              <el-icon><CircleCheckFilled /></el-icon>
              <span>{{ draft.completion_note || `${draft.synced_platforms || '平台'}草稿已就绪` }}</span>
            </div>

            <div class="card-title">{{ draft.title || '（未命名草稿）' }}</div>

            <div v-if="draft.body" class="card-snippet">{{ draft.body }}</div>

            <div class="card-meta">
              <span v-if="draft.files && draft.files.length > 0">📁 包含 {{ draft.files.length }} 个文件</span>
              <span v-if="draft.tags && draft.tags.length > 0" style="margin-left: 10px;">🏷️ {{ draft.tags.join(', ') }}</span>
            </div>

            <div class="card-actions">
              <div class="left-actions">
                <el-popconfirm
                  :title="draft.status === 'completed' ? '该作品已在百家号/平台完结存稿。确定从本地列表移除此记录吗？' : '确定彻底删除此草稿吗？'"
                  :confirm-button-text="draft.status === 'completed' ? '移除记录' : '删除'"
                  cancel-button-text="取消"
                  confirm-button-type="danger"
                  @confirm="deleteDraftItem(draft.id, draft.status === 'completed')"
                >
                  <template #reference>
                    <el-button size="small" :type="draft.status === 'completed' ? 'info' : 'danger'" text :icon="Delete">
                      {{ draft.status === 'completed' ? '移除记录' : '删除' }}
                    </el-button>
                  </template>
                </el-popconfirm>

                <el-button 
                  size="small" 
                  :type="draft.status === 'completed' ? 'warning' : 'success'" 
                  text 
                  @click="toggleDraftStatus(draft)"
                >
                  {{ draft.status === 'completed' ? '转为草稿' : '标为完结' }}
                </el-button>
              </div>

              <div class="right-actions">
                <template v-if="draft.status === 'completed'">
                  <el-button
                    size="small"
                    type="success"
                    plain
                    :icon="DocumentCopy"
                    @click="loadDraftIntoEditor(draft, 'copy')"
                  >
                    以此创建新内容
                  </el-button>
                  <el-button
                    size="small"
                    type="info"
                    text
                    :icon="Edit"
                    @click="loadDraftIntoEditor(draft, 'edit')"
                  >
                    查看/编辑
                  </el-button>
                </template>
                <template v-else>
                  <el-button
                    size="small"
                    type="primary"
                    plain
                    :icon="Edit"
                    @click="loadDraftIntoEditor(draft, 'edit')"
                  >
                    {{ currentDraftId === draft.id ? '当前正在编辑' : '打开编辑此草稿' }}
                  </el-button>
                </template>
              </div>
            </div>
          </div>
        </div>
      </div>
    </el-drawer>

    <!-- 素材库弹窗 -->
    <el-dialog
      v-model="materialLibraryVisible"
      title="从素材库导入素材"
      width="600px"
      class="material-dialog"
    >
      <div v-if="materials.length === 0" class="empty-material-tip">
        <el-empty description="素材库暂无文件" />
      </div>
      <div v-else class="material-grid-dialog">
        <div
          v-for="m in materials"
          :key="m.id"
          class="material-select-item"
          :class="{ 'is-selected': selectedMaterialIds.includes(m.id) }"
          @click="toggleSelectMaterial(m.id)"
        >
          <div class="mat-name">{{ m.filename }}</div>
          <div class="mat-size">{{ m.filesize }} MB</div>
        </div>
      </div>
      <template #footer>
        <el-button @click="materialLibraryVisible = false">取消</el-button>
        <el-button type="primary" @click="confirmMaterialSelection">导入选中 ({{ selectedMaterialIds.length }})</el-button>
      </template>
    </el-dialog>

    <!-- 图片大图预览 -->
    <el-image-viewer
      v-if="viewerVisible"
      :url-list="[previewUrl]"
      @close="viewerVisible = false"
    />
  </div>
</template>

<script setup>
import { ref, reactive, computed, onMounted, watch } from 'vue'
import {
  Upload, Plus, Close, Folder, Document, Delete, Edit, Refresh,
  UploadFilled, FolderOpened, Promotion, EditPen, ZoomIn, Top,
  ArrowLeft, ArrowRight, VideoCamera, CircleCheckFilled, DocumentCopy
} from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { useAccountStore } from '@/stores/account'
import { useAppStore } from '@/stores/app'
import { materialApi } from '@/api/material'
import { accountApi } from '@/api/account'
import { http } from '@/utils/request'

// API base URL
const apiBaseUrl = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5409'

const accountStore = useAccountStore()
const appStore = useAppStore()

// 平台定义
const platforms = [
  { key: 1, name: '小红书', icon: '📕' },
  { key: 3, name: '抖音', icon: '🎵' },
  { key: 4, name: '快手', icon: '⚡' },
  { key: 5, name: '百家号', icon: '📰' },
  { key: 6, name: 'B站', icon: '📺' },
  { key: 2, name: '视频号', icon: '🟢' },
  { key: 7, name: '今日头条', icon: '🗞️' },
  { key: 8, name: '搜狐', icon: '🦊' },
  { key: 9, name: '知乎', icon: '💡' },
  { key: 10, name: '百度贴吧', icon: '🟨' }
]

const getPlatformName = (type) => {
  const p = platforms.find(item => item.key === Number(type))
  return p ? p.name : `平台${type}`
}

// 支持「图文」文章形态的平台
const articleCapablePlatforms = [1, 2, 5, 7, 8, 9, 10]
const articleOnlyPlatforms = [8, 9]

// 表单响应式核心状态
const form = reactive({
  title: '',
  articleBody: '',
  selectedPlatform: 1, // 默认选中小红书
  contentType: 'article', // article | video
  fileList: [], // [{ name, path, url, size }]
  selectedAccounts: [],
  selectedTopics: [],
  aiGenerated: true,
  dryRun: false,
  isOriginal: true,
  isDraft: true,
  bilibiliCreationStatement: 1,
  workStatement: '',
  tiebaFid: '',
  tiebaKw: ''
})

// 草稿与会话状态
const currentDraftId = ref(null)
const currentDraftTitle = ref('')
const draftDrawerVisible = ref(false)
const draftList = ref([])
const draftFilterTab = ref('draft')
const loadingDrafts = ref(false)
const savingDraft = ref(false)
const publishing = ref(false)
const publishStatus = ref(null)

const pendingDraftsCount = computed(() => draftList.value.filter(d => d.status !== 'completed').length)
const completedDraftsCount = computed(() => draftList.value.filter(d => d.status === 'completed').length)
const filteredDraftList = computed(() => {
  if (draftFilterTab.value === 'draft') {
    return draftList.value.filter(d => d.status !== 'completed')
  }
  if (draftFilterTab.value === 'completed') {
    return draftList.value.filter(d => d.status === 'completed')
  }
  return draftList.value
})

// 话题输入与预设
const newTopicInput = ref('')
const recommendedTopics = ['河狸云', '蓝枢', '图纸协同', '工程管理', '工程数字化', 'CAD 看图']

// 拖拽与素材状态
const isDraggingOver = ref(false)
const draggedImageIndex = ref(null)
const fileInputRef = ref(null)
const videoInputRef = ref(null)
const materialLibraryVisible = ref(false)
const selectedMaterialIds = ref([])
const materials = computed(() => appStore.materials)

// 图片预览
const viewerVisible = ref(false)
const previewUrl = ref('')

// 声明选项列表
const bilibiliCreationOptions = [
  { label: '含 AI 生成内容', value: 1 },
  { label: '含虚构演绎内容', value: 2 },
  { label: '内容含营销信息', value: 3 },
  { label: '个人观点，仅供参考', value: 4 },
  { label: '内容无需标注', value: -1 }
]

const toutiaoWorkStatementOptions = [
  '取材网络', '引用站内', '个人观点，仅供参考', '引用AI',
  '虚构演绎，故事经历', '投资观点，仅供参考', '健康医疗分享，仅供参考'
]

// 计算属性：当前是否处于图文模式
const isArticleMode = computed(() => {
  if (articleOnlyPlatforms.includes(form.selectedPlatform)) return true
  if (form.selectedPlatform === 1) return form.contentType === 'article'
  return form.contentType === 'article' && articleCapablePlatforms.includes(form.selectedPlatform)
})

// 计算属性：标题字数上限
const maxTitleLength = computed(() => {
  if (form.selectedPlatform === 1) return 20 // 小红书
  if (form.selectedPlatform === 2 && isArticleMode.value) return 22 // 微信视频号图文
  if (form.selectedPlatform === 8) return 72 // 搜狐
  return 100
})

// 计算属性：图片张数上限
const maxImageCount = computed(() => {
  if (form.selectedPlatform === 2) return 18 // 微信视频号图文最多18张
  return 9
})

const isTitleOverLimit = computed(() => form.title.length > maxTitleLength.value)

// 计算属性：当前平台可用账号列表
const availableAccounts = computed(() => {
  const currentPlatformObj = platforms.find(p => p.key === form.selectedPlatform)
  if (!currentPlatformObj) return []
  return accountStore.accounts.filter(acc => acc.platform === currentPlatformObj.name)
})

// 当可用账号列表变化时：如果当前平台只有 1 个账号，自动默认选中它
watch(
  availableAccounts,
  (accounts) => {
    if (accounts && accounts.length === 1) {
      // 只有一个选项时，自动默认选中
      form.selectedAccounts = [accounts[0].id]
    } else if (accounts && accounts.length > 1) {
      // 若有多个账号，仅保留属于当前平台的已选账号
      form.selectedAccounts = form.selectedAccounts.filter(id =>
        accounts.some(acc => acc.id === id)
      )
    } else {
      form.selectedAccounts = []
    }
  },
  { immediate: true, deep: true }
)

// 切换平台
const switchPlatform = (key) => {
  form.selectedPlatform = key
  if (articleOnlyPlatforms.includes(key)) {
    form.contentType = 'article'
  } else if (key === 1) {
    form.contentType = 'article' // 小红书默认图文
  } else if (!articleCapablePlatforms.includes(key)) {
    form.contentType = 'video' // 纯视频平台
  }
}

// 话题增删
const addTopic = () => {
  const t = newTopicInput.value.replace(/#/g, '').trim()
  if (t && !form.selectedTopics.includes(t)) {
    form.selectedTopics.push(t)
  }
  newTopicInput.value = ''
}

const removeTopic = (index) => {
  form.selectedTopics.splice(index, 1)
}

const toggleTopic = (topic) => {
  const idx = form.selectedTopics.indexOf(topic)
  if (idx > -1) {
    form.selectedTopics.splice(idx, 1)
  } else {
    form.selectedTopics.push(topic)
  }
}

// 拖拽投放事件处理
const onDragOver = (e) => {
  isDraggingOver.value = true
}

const onDragLeave = (e) => {
  isDraggingOver.value = false
}

// 核心：处理文件与外部网页图片拖拽投递
const onFileDrop = async (e) => {
  isDraggingOver.value = false
  const dt = e.dataTransfer
  if (!dt) return

  // 1. 如果有本地文件
  if (dt.files && dt.files.length > 0) {
    await uploadLocalFiles(Array.from(dt.files))
    return
  }

  // 2. 如果是从外部网页拖入的图片（URL 链接或 HTML 标签）
  const uriList = dt.getData('text/uri-list')
  const htmlData = dt.getData('text/html')
  let targetUrl = ''

  if (uriList && (uriList.startsWith('http://') || uriList.startsWith('https://') || uriList.startsWith('data:image/'))) {
    targetUrl = uriList.split('\n')[0].trim()
  } else if (htmlData) {
    const match = htmlData.match(/<img[^>]+src=["']([^"']+)["']/i)
    if (match && match[1]) {
      targetUrl = match[1]
    }
  }

  if (targetUrl) {
    await downloadAndAttachRemoteImage(targetUrl)
  } else {
    ElMessage.info('未检测到有效的图片文件或网页图片链接')
  }
}

// 全局粘贴处理（Cmd+V / Ctrl+V）
const handleGlobalPaste = async (e) => {
  const clipboard = e.clipboardData
  if (!clipboard || !clipboard.items) return

  const imageItems = []
  for (let i = 0; i < clipboard.items.length; i++) {
    const item = clipboard.items[i]
    if (item.type.indexOf('image') !== -1) {
      const file = item.getAsFile()
      if (file) imageItems.push(file)
    }
  }

  if (imageItems.length > 0) {
    e.preventDefault()
    ElMessage.info(`检测到剪贴板图片，正在上传...`)
    await uploadLocalFiles(imageItems)
  }
}

// 本地文件上传逻辑
const uploadLocalFiles = async (files) => {
  for (const file of files) {
    if (isArticleMode.value && form.fileList.length >= maxImageCount.value) {
      ElMessage.warning(`图文模式最多支持 ${maxImageCount.value} 张图片`)
      break
    }
    const formData = new FormData()
    formData.append('file', file)
    try {
      const res = await http.post('/upload', formData, {
        headers: { 'Content-Type': 'multipart/form-data' }
      })
      if (res && res.code === 200) {
        const filename = res.data
        const fileObj = {
          name: file.name,
          filename: filename,
          url: `${apiBaseUrl}/getFile?filename=${filename}`,
          path: filename,
          size: file.size
        }
        if (!isArticleMode.value) {
          form.fileList = [fileObj]
        } else {
          form.fileList.push(fileObj)
        }
      }
    } catch (err) {
      console.error('上传文件失败:', err)
      ElMessage.error(`上传文件 ${file.name} 失败`)
    }
  }
  ElMessage.success('素材已成功导入')
}

// 远程网页图片代理抓取转存
const downloadAndAttachRemoteImage = async (imgUrl) => {
  if (isArticleMode.value && form.fileList.length >= maxImageCount.value) {
    ElMessage.warning(`图文模式最多支持 ${maxImageCount.value} 张图片`)
    return
  }
  const loading = ElMessage.info({ message: '正在抓取网页图片并转存本地...', duration: 0 })
  try {
    const res = await http.post('/downloadRemoteImage', { url: imgUrl })
    loading.close()
    if (res && res.code === 200 && res.data) {
      const data = res.data
      const fileObj = {
        name: data.name || data.filename,
        filename: data.filename,
        url: data.url,
        path: data.filename,
        size: data.size || 0
      }
      if (!isArticleMode.value) {
        form.fileList = [fileObj]
      } else {
        form.fileList.push(fileObj)
      }
      ElMessage.success('网页图片抓取转存成功')
    } else {
      ElMessage.error(res?.msg || '抓取远程图片失败')
    }
  } catch (err) {
    loading.close()
    console.error('抓取远程图片异常:', err)
    ElMessage.error('无法抓取该远程图片，请尝试保存到本地后拖拽上传')
  }
}

// 触发本地文件选择
const triggerFileInput = () => {
  fileInputRef.value?.click()
}

const handleFileInputChange = (e) => {
  const files = e.target.files
  if (files && files.length > 0) {
    uploadLocalFiles(Array.from(files))
  }
  e.target.value = ''
}

const triggerVideoInput = () => {
  videoInputRef.value?.click()
}

const handleVideoInputChange = (e) => {
  const files = e.target.files
  if (files && files.length > 0) {
    uploadLocalFiles([files[0]])
  }
  e.target.value = ''
}

// 图片网格拖拽排序
const onImageDragStart = (index, e) => {
  draggedImageIndex.value = index
  e.dataTransfer.effectAllowed = 'move'
}

const onImageDragOver = (index, e) => {
  e.dataTransfer.dropEffect = 'move'
}

const onImageDrop = (targetIndex, e) => {
  if (draggedImageIndex.value === null || draggedImageIndex.value === targetIndex) return
  const item = form.fileList.splice(draggedImageIndex.value, 1)[0]
  form.fileList.splice(targetIndex, 0, item)
  draggedImageIndex.value = null
  ElMessage.success('已调整图片排序')
}

const onImageDragEnd = () => {
  draggedImageIndex.value = null
}

// 设为封面（调换至第 0 位）
const setAsCover = (index) => {
  if (index <= 0) return
  const item = form.fileList.splice(index, 1)[0]
  form.fileList.unshift(item)
  ElMessage.success('已将此图片设为首图封面')
}

// 左右平移图片
const moveImage = (index, offset) => {
  const target = index + offset
  if (target < 0 || target >= form.fileList.length) return
  const item = form.fileList.splice(index, 1)[0]
  form.fileList.splice(target, 0, item)
}

// 移除单张图片
const removeImage = (index) => {
  form.fileList.splice(index, 1)
}

// 预览大图
const previewImage = (url) => {
  previewUrl.value = url
  viewerVisible.value = true
}

// 素材库导入
const selectMaterialLibrary = async () => {
  if (materials.value.length === 0) {
    try {
      const res = await materialApi.getAllMaterials()
      if (res.code === 200) appStore.setMaterials(res.data)
    } catch (e) {
      ElMessage.error('获取素材库失败')
      return
    }
  }
  selectedMaterialIds.value = []
  materialLibraryVisible.value = true
}

const toggleSelectMaterial = (id) => {
  const idx = selectedMaterialIds.value.indexOf(id)
  if (idx > -1) {
    selectedMaterialIds.value.splice(idx, 1)
  } else {
    selectedMaterialIds.value.push(id)
  }
}

const confirmMaterialSelection = () => {
  if (selectedMaterialIds.value.length === 0) {
    ElMessage.warning('请选择至少一个素材')
    return
  }
  selectedMaterialIds.value.forEach(id => {
    const m = materials.value.find(item => item.id === id)
    if (m) {
      const fileInfo = {
        name: m.filename,
        filename: m.file_path ? m.file_path.split('/').pop() : m.filename,
        url: materialApi.getMaterialPreviewUrl(m.file_path ? m.file_path.split('/').pop() : m.filename),
        path: m.file_path,
        size: (m.filesize || 0) * 1024 * 1024
      }
      if (!isArticleMode.value) {
        form.fileList = [fileInfo]
      } else if (form.fileList.length < maxImageCount.value) {
        form.fileList.push(fileInfo)
      }
    }
  })
  materialLibraryVisible.value = false
  ElMessage.success('素材已导入')
}

// 草稿管理
const openDraftDrawer = () => {
  draftDrawerVisible.value = true
  fetchDrafts()
}

const fetchDrafts = async () => {
  loadingDrafts.value = true
  try {
    const res = await http.get('/getDrafts')
    if (res && res.code === 200 && Array.isArray(res.data)) {
      draftList.value = res.data
    } else {
      draftList.value = []
    }
  } catch (e) {
    console.error('获取草稿失败:', e)
  } finally {
    loadingDrafts.value = false
  }
}

// 存为草稿
const saveCurrentDraft = async () => {
  if (savingDraft.value) return

  const hasTitle = !!form.title.trim()
  const hasBody = !!form.articleBody.trim()
  const hasFiles = form.fileList.length > 0

  if (!hasTitle && !hasBody && !hasFiles) {
    ElMessage.warning('草稿内容为空，请至少输入标题、正文或上传素材后再保存')
    return
  }

  savingDraft.value = true
  try {
    const payload = {
      id: currentDraftId.value || undefined,
      title: form.title.trim(),
      platform_type: form.selectedPlatform,
      contentType: form.contentType,
      body: form.articleBody,
      tags: form.selectedTopics,
      files: form.fileList,
      account_ids: form.selectedAccounts,
      aiGenerated: form.aiGenerated,
      dryRun: form.dryRun
    }
    const res = await http.post('/saveDraft', payload)
    if (res && res.code === 200) {
      if (res.data?.id) {
        currentDraftId.value = res.data.id
        currentDraftTitle.value = form.title.trim() || '未命名草稿'
      }
      ElMessage.success('草稿保存成功')
      fetchDrafts()
    } else {
      ElMessage.error(res?.msg || '保存草稿失败')
    }
  } catch (err) {
    console.error('保存草稿出错:', err)
    ElMessage.error('保存草稿失败')
  } finally {
    savingDraft.value = false
  }
}

// 载入草稿到编辑器（mode: 'edit' 为编辑原草稿，'copy' 为以此作为新创作副本）
const loadDraftIntoEditor = (draft, mode = 'edit') => {
  if (!draft) return
  if (mode === 'copy') {
    currentDraftId.value = null
    currentDraftTitle.value = ''
  } else {
    currentDraftId.value = draft.id
    currentDraftTitle.value = draft.title || '未命名草稿'
  }

  form.selectedPlatform = Number(draft.platform_type) || 1
  form.contentType = draft.content_type || 'article'
  form.title = draft.title || ''
  form.articleBody = draft.body || ''
  form.selectedTopics = Array.isArray(draft.tags) ? [...draft.tags] : []
  form.fileList = Array.isArray(draft.files) ? [...draft.files] : []
  form.selectedAccounts = Array.isArray(draft.account_ids) ? [...draft.account_ids] : []
  form.aiGenerated = draft.ai_generated !== 0
  form.dryRun = !!draft.dry_run
  form.isDraft = draft.is_draft !== undefined ? !!draft.is_draft : true

  draftDrawerVisible.value = false
  if (mode === 'copy') {
    ElMessage.success(`已载入「${draft.title || '作品'}」为新画板，修改不会覆盖原完结草稿`)
  } else {
    ElMessage.success(`已载入草稿「${currentDraftTitle.value}」`)
  }
}

// 切换草稿完结状态（手动在编写中与已完结之间切换）
const toggleDraftStatus = async (draft) => {
  if (!draft?.id) return
  const isCompleted = draft.status === 'completed'
  const newStatus = isCompleted ? 'draft' : 'completed'
  const completionNote = isCompleted ? '' : '已手动标为完结'
  try {
    const res = await http.post('/updateDraftStatus', {
      id: draft.id,
      status: newStatus,
      completion_note: completionNote
    })
    if (res && res.code === 200) {
      ElMessage.success(isCompleted ? '已转回【编写中】草稿' : '已成功标记为【已完结】')
      fetchDrafts()
    } else {
      ElMessage.error(res?.msg || '更新状态失败')
    }
  } catch (e) {
    ElMessage.error('更新状态失败')
  }
}

// 删除草稿 / 移除记录
const deleteDraftItem = async (draftId, isCompleted = false) => {
  try {
    const res = await http.post('/deleteDraft', { id: draftId })
    if (res && res.code === 200) {
      ElMessage.success(isCompleted ? '已从本地移除此完结记录' : '草稿已删除')
      if (currentDraftId.value === draftId) {
        currentDraftId.value = null
        currentDraftTitle.value = ''
      }
      fetchDrafts()
    }
  } catch (e) {
    ElMessage.error('删除草稿失败')
  }
}

// 新建创作（清空）
const handleNewCreation = () => {
  const hasContent = !!form.title || !!form.articleBody || form.fileList.length > 0
  if (hasContent) {
    ElMessageBox.confirm('是否清空当前内容开启全新创作？未保存的内容将会丢失。', '提示', {
      confirmButtonText: '确定新建',
      cancelButtonText: '取消',
      type: 'warning'
    }).then(() => {
      resetEditor()
      ElMessage.info('已开启新画板')
    }).catch(() => {})
  } else {
    resetEditor()
  }
}

const resetEditor = () => {
  currentDraftId.value = null
  currentDraftTitle.value = ''
  form.title = ''
  form.articleBody = ''
  form.fileList = []
  form.selectedTopics = []
  form.selectedAccounts = availableAccounts.value.length === 1 ? [availableAccounts.value[0].id] : []
  form.isDraft = true
  publishStatus.value = null
}

// 确认发布
const confirmPublish = async () => {
  if (publishing.value) return

  const isXhs = form.selectedPlatform === 1
  const isTencent = form.selectedPlatform === 2
  const isXhsArticle = isXhs && isArticleMode.value
  const isTencentArticle = isTencent && isArticleMode.value
  const isBaijiahaoArticle = form.selectedPlatform === 5 && isArticleMode.value
  const isSohuArticle = form.selectedPlatform === 8 && isArticleMode.value
  const isZhihuArticle = form.selectedPlatform === 9 && isArticleMode.value
  const isToutiaoArticle = form.selectedPlatform === 7 && isArticleMode.value

  // 1. 素材校验
  if (isArticleMode.value) {
    if (isXhsArticle) {
      if (form.fileList.length === 0) {
        ElMessage.error('小红书图文模式必须上传至少 1 张图片')
        return
      }
      if (form.fileList.length > 9) {
        ElMessage.error('小红书图文最多支持 9 张图片')
        return
      }
    } else if (isTencentArticle) {
      if (form.fileList.length === 0) {
        ElMessage.error('视频号图文模式必须上传至少 1 张图片')
        return
      }
      if (form.fileList.length > 18) {
        ElMessage.error('视频号图文最多支持 18 张图片')
        return
      }
    }
  } else {
    if (form.fileList.length === 0) {
      ElMessage.error('请先上传视频文件')
      return
    }
  }

  // 2. 正文校验（小红书与视频号图文正文可仅含话题，不强制必填大段正文）
  if (isArticleMode.value && !isXhsArticle && !isTencentArticle && !form.articleBody.trim()) {
    ElMessage.error('请输入文章正文内容')
    return
  }

  // 3. 标题校验
  if (isXhs && form.title.trim().length > 20) {
    ElMessage.error('小红书笔记标题不能超过 20 个字')
    return
  }
  if (isTencentArticle && form.title.trim().length > 22) {
    ElMessage.error('视频号图文标题不能超过 22 个字')
    return
  }
  if (isBaijiahaoArticle && form.title.trim().length < 2) {
    ElMessage.error('百家号图文标题至少 2 个字')
    return
  }
  if (isSohuArticle) {
    const len = form.title.trim().length
    if (len < 5 || len > 72) {
      ElMessage.error('搜狐号文章标题需 5-72 个字')
      return
    }
  }
  if (isZhihuArticle && !form.title.trim()) {
    ElMessage.error('请输入知乎文章标题')
    return
  }
  if (![1, 2, 3, 4].includes(form.selectedPlatform) && !form.title.trim()) {
    ElMessage.error('请输入作品标题')
    return
  }

  // 4. 账号校验
  if (form.selectedAccounts.length === 0) {
    ElMessage.error('请选择至少一个发布账号')
    return
  }

  publishing.value = true

  // 构造发布数据（完全兼容后端）
  const publishData = {
    type: form.selectedPlatform,
    title: form.title,
    tags: form.selectedTopics,
    fileList: form.fileList.map(f => f.path || f.filename),
    accountList: form.selectedAccounts.map(accId => {
      const acc = accountStore.accounts.find(a => a.id === accId)
      return acc ? acc.filePath : accId
    }),
    enableTimer: 0,
    videosPerDay: 1,
    dailyTimes: ['10:00'],
    startDays: 0,
    category: form.isOriginal ? 1 : 0,
    productLink: '',
    productTitle: '',
    isDraft: ![4, 6].includes(form.selectedPlatform) ? (form.isDraft !== false) : false,
    aiGenerated: [1, 2, 3, 4, 5, 9].includes(form.selectedPlatform) ? !!form.aiGenerated : false,
    bilibiliCreationStatement: form.selectedPlatform === 6 ? Number(form.bilibiliCreationStatement) : null,
    dryRun: form.selectedPlatform === 6 ? false : !!form.dryRun,
    contentType: articleOnlyPlatforms.includes(form.selectedPlatform)
      ? 'article'
      : (articleCapablePlatforms.includes(form.selectedPlatform) ? (form.contentType || 'video') : 'video'),
    articleBody: isArticleMode.value ? form.articleBody : '',
    coverImage: isArticleMode.value && form.fileList.length > 0 ? (form.fileList[0].path || form.fileList[0].filename) : '',
    coverImages: isArticleMode.value ? form.fileList.map(f => f.path || f.filename) : [],
    thumbnail: isArticleMode.value && form.fileList.length > 0 ? (form.fileList[0].path || form.fileList[0].filename) : '',
    workStatement: form.workStatement || '',
    workStatements: form.workStatement ? [form.workStatement] : [],
    tiebaFid: form.tiebaFid || '',
    tiebaKw: form.tiebaKw || ''
  }

  try {
    const res = await http.post('/postVideo', publishData)
    let msg = res?.msg || '发布任务已提交并在后台执行'
    let sType = 'success'

    if (form.dryRun) {
      msg = '任务已提交：仅预览不发布模式（浏览器会自动填表，不点击发布）'
      sType = 'warning'
    } else if ([4, 6].includes(form.selectedPlatform)) {
      msg = res?.msg || '任务已提交：该平台无官方草稿箱机制，已直接提交发布并在后台执行'
      sType = 'info'
    } else if (form.isDraft) {
      msg = res?.msg || '发布任务已提交：已保存至各平台官方草稿箱，请前往对应平台或手机 APP 确认后发布'
      sType = 'success'
    } else {
      msg = res?.msg || '发布任务已提交，正在后台执行'
      sType = 'info'
    }

    publishStatus.value = { message: msg, type: sType }
    ElMessage({ message: msg, type: sType, duration: 6000 })

    // 若当前正在编辑已有草稿，发布/存草稿成功后自动转为已完结归档状态（保留记录，不再直接物理删除）
    if (currentDraftId.value && !form.dryRun) {
      const platformName = getPlatformName(form.selectedPlatform)
      const note = form.isDraft ? `已保存至${platformName}官方草稿箱` : `已发布至${platformName}`
      http.post('/updateDraftStatus', {
        id: currentDraftId.value,
        status: 'completed',
        completion_note: note,
        synced_platforms: platformName
      }).catch(e => console.error('更新草稿状态失败:', e))
      currentDraftId.value = null
      currentDraftTitle.value = ''
      fetchDrafts()
    }
  } catch (err) {
    console.error('发布失败:', err)
    const errText = err?.response?.data?.msg || err.message || '发布请求失败'
    publishStatus.value = { message: `发布失败: ${errText}`, type: 'error' }
    ElMessage.error(`发布失败: ${errText}`)
  } finally {
    publishing.value = false
  }
}

onMounted(() => {
  fetchDrafts()
  if (accountStore.accounts.length === 0) {
    accountApi.getAccounts().then(res => {
      if (res.code === 200 && res.data) accountStore.setAccounts(res.data)
    }).catch(e => console.error(e))
  }

  // 检查是否从「发布历史」点击了「编辑重发」或「载入草稿」
  const retryDataStr = sessionStorage.getItem('retry_publish_data')
  if (retryDataStr) {
    try {
      const data = JSON.parse(retryDataStr)
      sessionStorage.removeItem('retry_publish_data')
      form.selectedPlatform = Number(data.platform_type) || 1
      form.contentType = data.content_type || 'article'
      form.title = data.title || ''
      form.articleBody = data.body || data.content || ''
      form.selectedTopics = Array.isArray(data.tags) ? [...data.tags] : []
      if (Array.isArray(data.account_ids) && data.account_ids.length > 0) {
        form.selectedAccounts = [...data.account_ids]
      }
      if (Array.isArray(data.files) && data.files.length > 0) {
        form.fileList = data.files.map(f => {
          const fn = typeof f === 'string' ? f.split('/').pop() : (f.filename || f.name || 'file')
          return {
            name: fn,
            filename: fn,
            path: fn,
            url: f.url || `${apiBaseUrl}/getFile?filename=${fn}`,
            size: f.size || 0
          }
        })
      }
      if (data.is_draft) {
        currentDraftId.value = data.id || null
        currentDraftTitle.value = data.title || ''
        ElMessage.success(`已载入草稿「${data.title || '草稿'}」到工作台`)
      } else {
        ElMessage.success('已载入内容，您可以修改后重新发布')
      }
    } catch (e) {
      console.error('载入数据失败:', e)
    }
  }
})
</script>

<style lang="scss" scoped>
.publish-studio {
  display: flex;
  flex-direction: column;
  min-height: calc(100vh - 60px);
  background: #f7f8fa;
  color: #1f2d3d;

  // 顶部工作栏
  .studio-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 24px;
    background: #ffffff;
    border-bottom: 1px solid #ebeef5;
    position: sticky;
    top: 0;
    z-index: 20;

    .header-left {
      display: flex;
      align-items: center;

      .editor-status-badge {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 14px;
        border-radius: 20px;
        font-size: 14px;
        font-weight: 500;
        background: #f0f2f5;
        color: #606266;

        &.is-draft {
          background: #fdf6ec;
          color: #e6a23c;
          border: 1px solid #faecd8;
        }

        .exit-draft-btn {
          margin-left: 8px;
          padding: 0;
          font-size: 12px;
        }
      }
    }

    .header-actions {
      display: flex;
      align-items: center;
      gap: 12px;

      .draft-badge {
        display: inline-flex;
      }

      .primary-publish-btn {
        font-weight: 600;
        padding: 0 20px;
      }
    }
  }

  // 工作台双栏
  .studio-body {
    display: flex;
    flex: 1;
    padding: 20px 24px;
    gap: 20px;
    align-items: flex-start;
  }

  // 左栏创作主体
  .content-panel {
    flex: 1;
    min-width: 0;
    display: flex;
    flex-direction: column;
    gap: 20px;

    .status-banner {
      margin-bottom: 4px;
    }

    .form-block {
      background: #ffffff;
      border: 1px solid #ebeef5;
      border-radius: 10px;
      padding: 20px 24px;

      .block-label {
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 15px;
        font-weight: 600;
        color: #303133;
        margin-bottom: 12px;

        .char-counter {
          font-size: 13px;
          font-weight: normal;
          color: #909399;

          &.text-danger {
            color: #f56c6c;
            font-weight: 600;
          }
        }
      }
    }

    .title-block {
      .title-input-field {
        :deep(.el-input__wrapper) {
          box-shadow: none !important;
          border-bottom: 2px solid #e4e7ed;
          border-radius: 0;
          padding-left: 0;
          font-size: 18px;
          font-weight: 600;

          &.is-focus {
            border-bottom-color: #409eff;
          }
        }
      }
    }

    .body-block {
      .body-textarea-field {
        :deep(.el-textarea__inner) {
          border-radius: 8px;
          font-size: 15px;
          line-height: 1.6;
          padding: 12px 14px;
        }
      }
    }

    // 拖拽投递区域
    .media-block, .video-block {
      .dropzone-area {
        border: 2px dashed #dcdfe6;
        border-radius: 10px;
        background: #fafbfc;
        padding: 30px 20px;
        text-align: center;
        cursor: pointer;
        transition: all 0.25s ease;

        &:hover, &.is-dragover {
          border-color: #409eff;
          background: #ecf5ff;
        }

        .dropzone-content {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 10px;

          .dropzone-icon {
            font-size: 44px;
            color: #909399;
          }

          .dropzone-text {
            strong {
              font-size: 16px;
              color: #303133;
            }

            p {
              margin: 6px 0 0;
              font-size: 13px;
              color: #909399;

              code {
                background: #eef1f6;
                padding: 2px 6px;
                border-radius: 4px;
                color: #409eff;
              }
            }
          }

          .dropzone-buttons {
            display: flex;
            gap: 12px;
            margin-top: 6px;
          }
        }
      }

      // 图片网格
      .image-grid-section {
        margin-top: 20px;

        .grid-tip {
          font-size: 13px;
          color: #606266;
          margin-bottom: 12px;
          background: #fdf6ec;
          padding: 8px 12px;
          border-radius: 6px;
          display: inline-block;
        }

        .image-cards-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
          gap: 16px;

          .image-card {
            position: relative;
            background: #ffffff;
            border: 1px solid #ebeef5;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04);
            transition: all 0.2s ease;
            cursor: grab;

            &:active {
              cursor: grabbing;
            }

            &.is-cover {
              border: 2px solid #e6a23c;
              box-shadow: 0 4px 12px rgba(230, 162, 60, 0.15);
            }

            &.is-dragging {
              opacity: 0.4;
              border-style: dashed;
            }

            .cover-tag {
              position: absolute;
              top: 8px;
              left: 8px;
              background: #e6a23c;
              color: #ffffff;
              font-size: 12px;
              font-weight: 600;
              padding: 2px 8px;
              border-radius: 4px;
              z-index: 5;
            }

            .index-tag {
              position: absolute;
              top: 8px;
              left: 8px;
              background: rgba(0, 0, 0, 0.55);
              color: #ffffff;
              font-size: 12px;
              font-weight: 600;
              width: 22px;
              height: 22px;
              line-height: 22px;
              text-align: center;
              border-radius: 50%;
              z-index: 5;
            }

            .image-thumb-wrap {
              width: 100%;
              height: 140px;
              background: #f5f7fa;
              position: relative;
              overflow: hidden;

              .image-thumb {
                width: 100%;
                height: 100%;
                object-fit: cover;
                display: block;
              }

              .thumb-hover-overlay {
                position: absolute;
                inset: 0;
                background: rgba(0, 0, 0, 0.35);
                display: flex;
                align-items: center;
                justify-content: center;
                color: #ffffff;
                font-size: 24px;
                opacity: 0;
                transition: opacity 0.2s;
              }

              &:hover .thumb-hover-overlay {
                opacity: 1;
              }
            }

            .image-card-footer {
              padding: 8px 10px;

              .file-name {
                display: block;
                font-size: 12px;
                color: #606266;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                margin-bottom: 6px;
              }

              .card-action-bar {
                display: flex;
                align-items: center;
                justify-content: flex-end;
                gap: 2px;
              }
            }
          }
        }
      }

      // 视频预览卡片
      .video-preview-card {
        display: flex;
        align-items: center;
        justify-content: space-between;
        background: #f5f7fa;
        border: 1px solid #e4e7ed;
        border-radius: 8px;
        padding: 16px 20px;

        .video-info {
          display: flex;
          align-items: center;
          gap: 14px;

          .video-file-icon {
            font-size: 32px;
            color: #409eff;
          }

          .file-meta {
            display: flex;
            flex-direction: column;

            .file-name {
              font-size: 15px;
              font-weight: 500;
              color: #303133;
            }

            .file-size {
              font-size: 13px;
              color: #909399;
              margin-top: 4px;
            }
          }
        }

        .video-actions {
          display: flex;
          align-items: center;
          gap: 16px;
        }
      }
    }
  }

  // 右栏设置面板
  .settings-panel {
    width: 340px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    gap: 16px;

    .setting-card {
      border-radius: 10px;
      border: 1px solid #ebeef5;

      .card-header-title {
        display: flex;
        align-items: center;
        justify-content: space-between;
        font-size: 15px;
        font-weight: 600;
        color: #303133;

        .manage-account-link {
          font-size: 13px;
          color: #409eff;
          text-decoration: none;

          &:hover {
            text-decoration: underline;
          }
        }
      }

      // 平台网格
      .platform-grid {
        display: grid;
        grid-template-columns: repeat(3, 1fr);
        gap: 10px;

        .platform-item {
          display: flex;
          flex-direction: column;
          align-items: center;
          justify-content: center;
          padding: 12px 6px;
          border: 1px solid #ebeef5;
          border-radius: 8px;
          background: #fafbfc;
          cursor: pointer;
          transition: all 0.2s;

          .platform-icon {
            font-size: 20px;
            margin-bottom: 4px;
          }

          .platform-name {
            font-size: 13px;
            color: #606266;
          }

          &:hover {
            border-color: #b3d8ff;
            background: #ecf5ff;
          }

          &.is-active {
            border-color: #409eff;
            background: #e6f1fc;
            box-shadow: 0 0 0 1px #409eff inset;

            .platform-name {
              color: #409eff;
              font-weight: 600;
            }
          }
        }
      }

      .sub-setting-row {
        display: flex;
        align-items: center;
        margin-top: 14px;
        padding-top: 12px;
        border-top: 1px dashed #ebeef5;

        .sub-label {
          font-size: 13px;
          color: #606266;
          margin-right: 10px;
        }
      }

      .no-account-tip {
        text-align: center;
        padding: 14px 0;
        color: #909399;
        font-size: 13px;

        p {
          margin-bottom: 10px;
        }
      }

      .account-selector {
        width: 100%;
      }

      .topics-input-wrap {
        .selected-tags-box {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
          margin: 10px 0;

          .topic-tag {
            font-size: 13px;
          }
        }

        .recommended-tags {
          margin-top: 12px;

          .rec-title {
            display: block;
            font-size: 12px;
            color: #909399;
            margin-bottom: 6px;
          }

          .rec-list {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;

            .rec-tag {
              cursor: pointer;
              transition: all 0.15s;

              &:hover {
                color: #409eff;
                border-color: #409eff;
              }
            }
          }
        }
      }

      .declarations-list {
        display: flex;
        flex-direction: column;
        gap: 10px;

        .setting-checkbox {
          font-size: 13px;
          color: #606266;
        }

        .sub-setting-block {
          margin-top: 6px;

          .sub-label {
            display: block;
            font-size: 12px;
            color: #606266;
            margin-bottom: 4px;
          }

          .full-width {
            width: 100%;
          }
        }
      }
    }
  }

  // 草稿箱抽屉
  .draft-drawer {
    .drawer-header-custom {
      display: flex;
      align-items: center;
      justify-content: space-between;
      width: 100%;
      padding-right: 14px;

      .drawer-title-group {
        display: flex;
        align-items: center;
        gap: 8px;
      }

      .drawer-title-text {
        font-size: 16px;
        font-weight: 600;
        color: #303133;
      }

      .drawer-completed-badge {
        font-size: 12px;
        padding: 2px 8px;
        background: #f0f9eb;
        color: #67c23a;
        border-radius: 10px;
        border: 1px solid #e1f3d8;
      }
    }

    .draft-drawer-body {
      padding: 0 4px;
    }

    .draft-tabs-filter {
      margin-bottom: 14px;
      display: flex;
      justify-content: center;
    }

    .draft-empty-wrap {
      padding: 30px 0;
    }

    .draft-cards-scroll {
      display: flex;
      flex-direction: column;
      gap: 14px;
    }

    .draft-item-card {
      background: #fafafa;
      border: 1px solid #ebeef5;
      border-radius: 8px;
      padding: 14px 16px;
      transition: all 0.2s;

      &:hover {
        background: #ffffff;
        border-color: #b3d8ff;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
      }

      &.is-current {
        border-color: #e6a23c;
        background: #fdfaf4;
      }

      &.is-completed {
        border-left: 4px solid #67c23a;
        background: #fafcf9;

        &:hover {
          background: #ffffff;
          border-color: #67c23a;
        }
      }

      .card-head {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 8px;

        .card-date {
          font-size: 12px;
          color: #909399;
        }
      }

      .card-completion-banner {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-bottom: 8px;
        padding: 4px 10px;
        background: #f0f9eb;
        border-radius: 4px;
        color: #529b2e;
        font-size: 12px;
        font-weight: 500;
      }

      .card-title {
        font-size: 15px;
        font-weight: 600;
        color: #1f2d3d;
        margin-bottom: 6px;
        word-break: break-all;
      }

      .card-snippet {
        font-size: 13px;
        color: #606266;
        line-height: 1.5;
        margin-bottom: 8px;
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
      }

      .card-meta {
        font-size: 12px;
        color: #909399;
        margin-bottom: 12px;
      }

      .card-actions {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 10px;
        border-top: 1px dashed #ebeef5;
        padding-top: 10px;

        .left-actions {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .right-actions {
          display: flex;
          align-items: center;
          gap: 8px;
        }
      }
    }
  }

  // 素材库弹窗
  .material-grid-dialog {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 12px;
    max-height: 400px;
    overflow-y: auto;

    .material-select-item {
      padding: 12px;
      border: 1px solid #ebeef5;
      border-radius: 6px;
      cursor: pointer;
      background: #fafbfc;

      &:hover {
        border-color: #409eff;
      }

      &.is-selected {
        border-color: #409eff;
        background: #ecf5ff;
      }

      .mat-name {
        font-size: 14px;
        font-weight: 500;
        color: #303133;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }

      .mat-size {
        font-size: 12px;
        color: #909399;
        margin-top: 4px;
      }
    }
  }
}
</style>
