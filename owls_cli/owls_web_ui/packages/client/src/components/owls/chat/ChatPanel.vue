<script setup lang="ts">
import { useChatStore } from '@/stores/owls/chat'
import { useSessionBrowserPrefsStore } from '@/stores/owls/session-browser-prefs'
import { NButton, NTooltip, useMessage } from 'naive-ui'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { getSourceLabel } from '@/shared/session-display'
import { copyToClipboard } from '@/utils/clipboard'
import ChatInput from './ChatInput.vue'
import ConversationMonitorPane from './ConversationMonitorPane.vue'
import MessageList from './MessageList.vue'

const chatStore = useChatStore()
const sessionBrowserPrefsStore = useSessionBrowserPrefsStore()
const message = useMessage()
const { t } = useI18n()

const currentMode = ref<'chat' | 'live'>('chat')

let mobileQuery: MediaQueryList | null = null
const isMobile = ref(false)

function handleModeChange(mode: 'chat' | 'live') {
  if (mode === currentMode.value) return
  currentMode.value = mode
}

function handleMobileChange(e: MediaQueryListEvent | MediaQueryList) {
  isMobile.value = e.matches
}

onMounted(() => {
  mobileQuery = window.matchMedia('(max-width: 768px)')
  handleMobileChange(mobileQuery)
  mobileQuery.addEventListener('change', handleMobileChange)
})

onUnmounted(() => {
  mobileQuery?.removeEventListener('change', handleMobileChange)
})

const activeSessionTitle = computed(() =>
  chatStore.activeSession?.title || t('chat.newChat'),
)

const headerTitle = computed(() =>
  currentMode.value === 'live' ? t('chat.liveSessions') : activeSessionTitle.value,
)

const activeSessionSource = computed(() =>
  currentMode.value === 'chat' ? (chatStore.activeSession?.source || '') : '',
)

function handleNewChat() {
  chatStore.newChat()
}

async function copySessionId(id?: string) {
  const sessionId = id || chatStore.activeSessionId
  if (sessionId) {
    const ok = await copyToClipboard(sessionId)
    if (ok) message.success(t('common.copied'))
    else message.error(t('common.copied') + ' ✗')
  }
}

</script>

<template>
  <div class="chat-panel">
    <div class="chat-main">
      <header class="chat-header">
        <div class="header-left">
          <span class="header-session-title">{{ headerTitle }}</span>
          <span v-if="activeSessionSource" class="source-badge">{{ getSourceLabel(activeSessionSource) }}</span>
        </div>
        <div class="header-actions">
          <div class="chat-mode-toggle">
            <NButton
              size="small"
              :type="currentMode === 'chat' ? 'primary' : 'default'"
              :aria-pressed="currentMode === 'chat'"
              @click="handleModeChange('chat')"
            >{{ t('chat.chatMode') }}</NButton>
            <NButton
              size="small"
              :type="currentMode === 'live' ? 'primary' : 'default'"
              :aria-pressed="currentMode === 'live'"
              @click="handleModeChange('live')"
            >{{ t('chat.liveMode') }}</NButton>
          </div>
          <template v-if="currentMode === 'chat'">
            <NTooltip trigger="hover">
              <template #trigger>
                <NButton quaternary size="small" @click="copySessionId()" circle>
                  <template #icon>
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                  </template>
                </NButton>
              </template>
              {{ t('chat.copySessionId') }}
            </NTooltip>
            <NButton size="small" :circle="isMobile" @click="handleNewChat">
              <template #icon>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
              </template>
              <template v-if="!isMobile">{{ t('chat.newChat') }}</template>
            </NButton>
          </template>
        </div>
      </header>

      <template v-if="currentMode === 'chat'">
        <MessageList />
        <ChatInput />
      </template>
      <ConversationMonitorPane v-else :human-only="sessionBrowserPrefsStore.humanOnly" />
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.chat-panel {
  display: flex;
  height: 100%;
  position: relative;
}

.chat-main {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}

.chat-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 21px 20px;
  border-bottom: 1px solid $border-color;
  flex-shrink: 0;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
  flex: 1;
  min-width: 0;
}

.header-session-title {
  font-size: 16px;
  font-weight: 600;
  color: $text-primary;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.source-badge {
  font-size: 10px;
  color: $text-muted;
  background: rgba($text-muted, 0.12);
  padding: 1px 7px;
  border-radius: 8px;
  flex-shrink: 0;
  white-space: nowrap;
  line-height: 16px;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.chat-mode-toggle {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-right: 4px;
}

@media (max-width: $breakpoint-mobile) {
  .chat-header {
    padding: 16px 12px 16px 52px;
  }
}
</style>
