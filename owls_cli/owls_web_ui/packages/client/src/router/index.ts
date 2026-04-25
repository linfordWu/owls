import { createRouter, createWebHashHistory } from 'vue-router'
import { hasApiKey } from '@/api/client'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      name: 'login',
      component: () => import('@/views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/owls/chat',
      name: 'owls.chat',
      component: () => import('@/views/owls/ChatView.vue'),
    },
    {
      path: '/owls/jobs',
      name: 'owls.jobs',
      component: () => import('@/views/owls/JobsView.vue'),
    },
    {
      path: '/owls/models',
      name: 'owls.models',
      component: () => import('@/views/owls/ModelsView.vue'),
    },
    {
      path: '/owls/profiles',
      name: 'owls.profiles',
      component: () => import('@/views/owls/ProfilesView.vue'),
      meta: { adminOnly: true },
    },
    {
      path: '/owls/logs',
      name: 'owls.logs',
      component: () => import('@/views/owls/LogsView.vue'),
      meta: { adminOnly: true },
    },
    {
      path: '/owls/usage',
      name: 'owls.usage',
      component: () => import('@/views/owls/UsageView.vue'),
      meta: { adminOnly: true },
    },
    {
      path: '/owls/skills',
      name: 'owls.skills',
      component: () => import('@/views/owls/SkillsView.vue'),
    },
    {
      path: '/owls/memory',
      name: 'owls.memory',
      component: () => import('@/views/owls/MemoryView.vue'),
    },
    {
      path: '/owls/settings',
      name: 'owls.settings',
      component: () => import('@/views/owls/SettingsView.vue'),
      meta: { adminOnly: true },
    },
    {
      path: '/owls/gateways',
      name: 'owls.gateways',
      component: () => import('@/views/owls/GatewaysView.vue'),
      meta: { adminOnly: true },
    },
    {
      path: '/owls/channels',
      redirect: '/owls/chat',
    },
    {
      path: '/owls/terminal',
      name: 'owls.terminal',
      component: () => import('@/views/owls/TerminalView.vue'),
      meta: { adminOnly: true },
    },
    {
      path: '/owls/files',
      name: 'owls.files',
      component: () => import('@/views/owls/FilesView.vue'),
      meta: { adminOnly: true },
    },
  ],
})

router.beforeEach(async (to, _from, next) => {
  // Public pages don't need auth
  if (to.meta.public) {
    // Already has key, skip login
    if (to.name === 'login' && hasApiKey()) {
      next({ path: '/owls/chat' })
      return
    }
    next()
    return
  }

  // All other pages require token
  if (!hasApiKey()) {
    next({ name: 'login' })
    return
  }

  if (to.meta.adminOnly) {
    const token = localStorage.getItem('owls_api_key') || ''
    try {
      const res = await fetch('/api/auth/status', {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      const status = res.ok ? await res.json() : null
      if (status?.role !== 'admin') {
        next({ path: '/owls/chat' })
        return
      }
    } catch {
      next({ path: '/owls/chat' })
      return
    }
  }

  next()
})

export default router
