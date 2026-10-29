import { http } from '@/utils/request'

// 产品管理 API
export const productApi = {
  // 获取所有产品
  getAllProducts: () => {
    return http.get('/api/products')
  },
  // 创建产品
  createProduct: (data) => {
    return http.post('/api/products', data)
  },
  // 更新产品
  updateProduct: (productId, data) => {
    return http.put(`/api/products/${productId}`, data)
  },
  // 删除产品
  deleteProduct: (productId) => {
    return http.delete(`/api/products/${productId}`)
  },
  // 获取产品详情
  getProduct: (productId) => {
    return http.get(`/api/products/${productId}`)
  },
}

// 知识库条目 API
export const knowledgeBaseApi = {
  // 获取知识库条目列表（支持 ?product_id= 过滤）
  getAllKnowledgeBase: (productId) => {
    return http.get('/api/knowledge-base', { params: productId ? { product_id: productId } : {} })
  },
  // 创建知识库条目
  createKnowledgeBase: (data) => {
    return http.post('/api/knowledge-base', data)
  },
  // 更新知识库条目
  updateKnowledgeBase: (kbId, data) => {
    return http.put(`/api/knowledge-base/${kbId}`, data)
  },
  // 删除知识库条目
  deleteKnowledgeBase: (kbId) => {
    return http.delete(`/api/knowledge-base/${kbId}`)
  },
  // 获取知识库条目详情
  getKnowledgeBase: (kbId) => {
    return http.get(`/api/knowledge-base/${kbId}`)
  },
  // 基于知识库条目生成双版本文案 + 五平台元数据
  generateContent: (kbId) => {
    return http.post('/api/generate-content', { kb_id: kbId })
  },
  // 一键发布（复用 /postVideo，默认存草稿）
  publishFromKb: (data) => {
    return http.post('/api/publish-from-kb', data)
  },
}

// 知识库条目文件上传（复用现有素材上传接口，返回文件路径后写入 knowledge_base.file_paths）
export const kbFileApi = {
  // 上传文件到服务器，返回存储路径
  uploadFile: (formData, onUploadProgress) => {
    return http.upload('/uploadSave', formData, onUploadProgress)
  },
}
