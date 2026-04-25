<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NInput } from 'naive-ui'
import { useAppStore } from '@/stores/owls/app'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const appStore = useAppStore()

const displayName = computed(() => appStore.currentUsername || t('login.tokenLogin'))

onMounted(() => {
  appStore.loadAuthStatus()
})
</script>

<template>
  <div class="profile-selector">
    <div class="selector-label">{{ t('login.username') }}</div>
    <NInput
      :value="displayName"
      size="small"
      readonly
    />
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.profile-selector {
  padding: 0 12px;
  margin-bottom: 8px;
}

.selector-label {
  font-size: 11px;
  font-weight: 600;
  color: $text-muted;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  margin-bottom: 6px;
}
</style>
