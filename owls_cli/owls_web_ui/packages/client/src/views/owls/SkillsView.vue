<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { NButton, NInput, useMessage } from 'naive-ui'
import { useI18n } from 'vue-i18n'
import SkillList from '@/components/owls/skills/SkillList.vue'
import SkillDetail from '@/components/owls/skills/SkillDetail.vue'
import { deleteSkill, fetchSkills, importSkill, type SkillCategory } from '@/api/owls/skills'

const { t } = useI18n()
const message = useMessage()
const categories = ref<SkillCategory[]>([])
const loading = ref(false)
const importing = ref(false)
const deletingSkillPath = ref('')
const selectedCategory = ref('')
const selectedSkill = ref('')
const selectedSkillPath = ref('')
const searchQuery = ref('')
const showSidebar = ref(true)
const skillInputRef = ref<HTMLInputElement | null>(null)
let mobileQuery: MediaQueryList | null = null

function handleMobileChange(e: MediaQueryListEvent | MediaQueryList) {
  showSidebar.value = !e.matches
}

onMounted(() => {
  mobileQuery = window.matchMedia('(max-width: 768px)')
  handleMobileChange(mobileQuery)
  mobileQuery.addEventListener('change', handleMobileChange)
  skillInputRef.value?.setAttribute('webkitdirectory', '')
  loadSkills()
})

onUnmounted(() => {
  mobileQuery?.removeEventListener('change', handleMobileChange)
})

async function loadSkills() {
  loading.value = true
  try {
    categories.value = await fetchSkills()
  } catch (err: any) {
    console.error('Failed to load skills:', err)
  } finally {
    loading.value = false
  }
}

function handleSelect(category: string, skill: string, skillPath: string) {
  selectedCategory.value = category
  selectedSkill.value = skill
  selectedSkillPath.value = skillPath
  if (window.innerWidth <= 768) {
    showSidebar.value = false
  }
}

function openSkillImport() {
  skillInputRef.value?.click()
}

async function handleSkillImport(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  if (files.length === 0) return

  importing.value = true
  try {
    const result = await importSkill(files)
    message.success(t('skills.importSuccess', { name: result.skill, count: result.files }))
    await loadSkills()
    selectedCategory.value = result.category
    selectedSkill.value = result.skill
    selectedSkillPath.value = `${result.category}/${result.skill}`
    if (window.innerWidth <= 768) showSidebar.value = false
  } catch (err: any) {
    message.error(err.message || t('skills.importFailed'))
  } finally {
    importing.value = false
    input.value = ''
  }
}

async function handleDelete(skillPath: string) {
  if (deletingSkillPath.value) return
  deletingSkillPath.value = skillPath
  try {
    await deleteSkill(skillPath)
    message.success(t('skills.deleteSuccess'))
    if (selectedSkillPath.value === skillPath) {
      selectedCategory.value = ''
      selectedSkill.value = ''
      selectedSkillPath.value = ''
    }
    await loadSkills()
  } catch (err: any) {
    message.error(err.message || t('skills.deleteFailed'))
  } finally {
    deletingSkillPath.value = ''
  }
}
</script>

<template>
  <div class="skills-view">
    <header class="page-header">
      <div style="display: flex; align-items: center; gap: 8px;">
        <h2 class="header-title">{{ t('skills.title') }}</h2>
        <button v-if="!showSidebar" class="sidebar-toggle" @click="showSidebar = true">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
        </button>
      </div>
      <div class="header-actions">
        <input
          ref="skillInputRef"
          type="file"
          multiple
          class="hidden-input"
          @change="handleSkillImport"
        />
        <NButton size="small" type="primary" :loading="importing" @click="openSkillImport">
          {{ t('skills.import') }}
        </NButton>
        <NInput
          v-model:value="searchQuery"
          :placeholder="t('skills.searchPlaceholder')"
          size="small"
          clearable
          class="search-input"
        />
      </div>
    </header>

    <div class="skills-content">
      <div v-if="loading && categories.length === 0" class="skills-loading">{{ t('common.loading') }}</div>
      <div v-else class="skills-layout">
          <div class="mobile-backdrop" :class="{ active: showSidebar }" @click="showSidebar = false" />
          <div v-if="showSidebar" class="skills-sidebar">
            <SkillList
              :categories="categories"
              :selected-skill="selectedSkillPath || null"
              :search-query="searchQuery"
              :deleting-skill="deletingSkillPath"
              @select="handleSelect"
              @delete="handleDelete"
            />
          </div>
          <div class="skills-main">
            <SkillDetail
              v-if="selectedCategory && selectedSkill"
              :category="selectedCategory"
              :skill="selectedSkill"
              :skill-path="selectedSkillPath"
            />
            <div v-else class="empty-detail">
              <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" opacity="0.2">
                <polygon points="12 2 2 7 12 12 22 7 12 2" />
                <polyline points="2 17 12 22 22 17" />
                <polyline points="2 12 12 17 22 12" />
              </svg>
              <span>{{ t('skills.noMatch') }}</span>
            </div>
          </div>
        </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.skills-view {
  height: calc(100 * var(--vh));
  display: flex;
  flex-direction: column;
}

.search-input {
  width: 160px;

  @media (max-width: $breakpoint-mobile) {
    width: 100%;
  }
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;

  @media (max-width: $breakpoint-mobile) {
    flex: 1;
    justify-content: flex-end;
    min-width: 0;
  }
}

.hidden-input {
  display: none;
}

.skills-content {
  flex: 1;
  overflow: hidden;
}

.skills-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  font-size: 13px;
  color: $text-muted;
}

.skills-layout {
  display: flex;
  height: 100%;
}

.skills-sidebar {
  width: 280px;
  border-right: 1px solid $border-color;
  flex-shrink: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.skills-main {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  min-width: 0;
}

.sidebar-toggle {
  display: none;
  border: none;
  background: none;
  cursor: pointer;
  color: $text-secondary;
  padding: 4px;
  border-radius: $radius-sm;

  &:hover {
    background: rgba(var(--accent-primary-rgb), 0.06);
  }
}

@media (max-width: $breakpoint-mobile) {
  .sidebar-toggle {
    display: flex;
  }

  .skills-sidebar {
    position: absolute;
    left: 0;
    top: 0;
    height: 100%;
    z-index: 10;
    background: $bg-card;
    box-shadow: 2px 0 8px rgba(0, 0, 0, 0.1);
  }

  .skills-layout {
    position: relative;
  }

  .mobile-backdrop {
    display: block;
    position: absolute;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    z-index: 9;
    opacity: 0;
    pointer-events: none;
    transition: opacity $transition-fast;

    &.active {
      opacity: 1;
      pointer-events: auto;
    }
  }
}

.empty-detail {
  height: 100%;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  color: $text-muted;
  font-size: 13px;
}
</style>
