import { createRouter, createWebHashHistory } from 'vue-router'
import PublishCenter from '../views/PublishCenter.vue'
import PublishHistory from '../views/PublishHistory.vue'
import AccountManagement from '../views/AccountManagement.vue'
import MaterialManagement from '../views/MaterialManagement.vue'
import DataCenter from '../views/DataCenter.vue'
import About from '../views/About.vue'

const routes = [
  {
    path: '/',
    redirect: '/publish-center'
  },
  {
    path: '/publish-center',
    name: 'PublishCenter',
    component: PublishCenter
  },
  {
    path: '/publish-history',
    name: 'PublishHistory',
    component: PublishHistory
  },
  {
    path: '/account-management',
    name: 'AccountManagement',
    component: AccountManagement
  },
  {
    path: '/material-management',
    name: 'MaterialManagement',
    component: MaterialManagement
  },
  {
    path: '/data-center',
    name: 'DataCenter',
    component: DataCenter
  },
  {
    path: '/about',
    name: 'About',
    component: About
  }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes
})

export default router