<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { NButton, NPagination, NPopconfirm, NSwitch, useMessage } from 'naive-ui'
import type { SkillCategory, SkillInfo } from '@/api/owls/skills'
import { toggleSkill } from '@/api/owls/skills'
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
const message = useMessage()

const props = defineProps<{
  categories: SkillCategory[]
  selectedSkill: string | null
  searchQuery: string
  deletingSkill?: string
}>()

const emit = defineEmits<{
  select: [category: string, skill: string, skillPath: string]
  delete: [skillPath: string]
}>()

const collapsedCategories = ref<Set<string>>(new Set())
const togglingSkills = ref<Set<string>>(new Set())
const currentPage = ref(1)
const SKILLS_PER_PAGE = 12

type SkillListEntry = {
  category: SkillCategory
  skill: SkillInfo
}

const filteredCategories = computed(() => {
  if (!props.searchQuery) return props.categories
  const q = props.searchQuery.toLowerCase()
  return props.categories
    .map(cat => {
      const categoryMatches = cat.name.toLowerCase().includes(q)
      return {
        ...cat,
        skills: categoryMatches
          ? cat.skills
          : cat.skills.filter(
            s => s.name.toLowerCase().includes(q) || s.description.toLowerCase().includes(q),
          ),
      }
    })
    .filter(cat => cat.skills.length > 0)
})

const filteredSkillEntries = computed<SkillListEntry[]>(() =>
  filteredCategories.value.flatMap(cat =>
    cat.skills.map(skill => ({ category: cat, skill })),
  ),
)

const totalSkills = computed(() => filteredSkillEntries.value.length)
const pageCount = computed(() => Math.max(1, Math.ceil(totalSkills.value / SKILLS_PER_PAGE)))
const showPagination = computed(() => totalSkills.value > SKILLS_PER_PAGE)
const pageStart = computed(() => totalSkills.value === 0 ? 0 : (currentPage.value - 1) * SKILLS_PER_PAGE + 1)
const pageEnd = computed(() => Math.min(currentPage.value * SKILLS_PER_PAGE, totalSkills.value))

const pagedCategories = computed<SkillCategory[]>(() => {
  const start = (currentPage.value - 1) * SKILLS_PER_PAGE
  const entries = filteredSkillEntries.value.slice(start, start + SKILLS_PER_PAGE)
  const grouped = new Map<string, SkillCategory>()

  for (const entry of entries) {
    const existing = grouped.get(entry.category.name)
    if (existing) {
      existing.skills.push(entry.skill)
    } else {
      grouped.set(entry.category.name, {
        ...entry.category,
        skills: [entry.skill],
      })
    }
  }

  return Array.from(grouped.values())
})

function getCategoryTotal(name: string): number {
  return filteredCategories.value.find(cat => cat.name === name)?.skills.length ?? 0
}

function toggleCategory(name: string) {
  if (collapsedCategories.value.has(name)) {
    collapsedCategories.value.delete(name)
  } else {
    collapsedCategories.value.add(name)
  }
}

function handleSelect(category: string, skill: SkillInfo) {
  emit('select', category, skill.name, skill.path)
}

function handleDelete(skill: SkillInfo) {
  emit('delete', skill.path)
}

async function handleToggle(category: string, skillName: string, newEnabled: boolean) {
  if (togglingSkills.value.has(skillName)) return
  togglingSkills.value.add(skillName)

  try {
    await toggleSkill(skillName, newEnabled)
    // Update local state
    const cat = props.categories.find(c => c.name === category)
    const skill = cat?.skills.find(s => s.name === skillName)
    if (skill) skill.enabled = newEnabled
  } catch (err: any) {
    message.error(t('skills.toggleFailed') + `: ${err.message}`)
  } finally {
    togglingSkills.value.delete(skillName)
  }
}

watch(() => props.searchQuery, () => {
  currentPage.value = 1
})

watch(
  [filteredSkillEntries, () => props.selectedSkill],
  () => {
    if (currentPage.value > pageCount.value) {
      currentPage.value = pageCount.value
    }

    if (!props.selectedSkill) return
    const selectedIndex = filteredSkillEntries.value.findIndex(
      entry => props.selectedSkill === entry.skill.path,
    )
    if (selectedIndex !== -1) {
      currentPage.value = Math.floor(selectedIndex / SKILLS_PER_PAGE) + 1
    }
  },
  { immediate: true },
)
</script>

<template>
  <div class="skill-list">
    <div class="skill-page">
      <div v-if="pagedCategories.length === 0" class="skill-empty">
        {{ searchQuery ? t('skills.noMatch') : t('skills.noSkills') }}
      </div>
      <div
        v-for="cat in pagedCategories"
        :key="cat.name"
        class="skill-category"
      >
        <button class="category-header" @click="toggleCategory(cat.name)">
          <svg
            width="12" height="12" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="2"
            class="category-arrow"
            :class="{ collapsed: collapsedCategories.has(cat.name) }"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
          <span class="category-name">{{ cat.name }}</span>
          <span class="category-count">{{ getCategoryTotal(cat.name) }}</span>
        </button>
        <div v-if="cat.description" class="category-description">
          {{ cat.description }}
        </div>
        <div v-if="!collapsedCategories.has(cat.name)" class="category-skills">
          <div
            v-for="skill in cat.skills"
            :key="skill.path"
            class="skill-item"
            :class="{
              active: selectedSkill === skill.path,
            }"
            role="button"
            tabindex="0"
            @click="handleSelect(cat.name, skill)"
            @keydown.enter="handleSelect(cat.name, skill)"
            @keydown.space.prevent="handleSelect(cat.name, skill)"
          >
            <div class="skill-info">
              <span class="skill-name">{{ skill.name }}</span>
              <span v-if="skill.description" class="skill-desc">{{ skill.description }}</span>
            </div>
            <div class="skill-actions" @click.stop>
              <NSwitch
                size="small"
                :value="skill.enabled !== false"
                :loading="togglingSkills.has(skill.name)"
                @update:value="handleToggle(cat.name, skill.name, $event)"
              />
              <NPopconfirm
                :positive-text="t('common.delete')"
                :negative-text="t('common.cancel')"
                @positive-click="handleDelete(skill)"
              >
                <template #trigger>
                  <NButton
                    size="tiny"
                    quaternary
                    circle
                    type="error"
                    class="skill-delete"
                    :loading="deletingSkill === skill.path"
                    :title="t('skills.delete')"
                  >
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                      <path d="M3 6h18" />
                      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                      <path d="M10 11v6" />
                      <path d="M14 11v6" />
                    </svg>
                  </NButton>
                </template>
                {{ t('skills.deleteConfirm', { name: skill.name }) }}
              </NPopconfirm>
            </div>
          </div>
        </div>
      </div>
    </div>
    <div v-if="showPagination" class="skill-pagination">
      <span class="pagination-range">{{ pageStart }}-{{ pageEnd }} / {{ totalSkills }}</span>
      <NPagination
        v-model:page="currentPage"
        :page-count="pageCount"
        :page-slot="4"
        size="small"
        simple
      />
    </div>
  </div>
</template>

<style scoped lang="scss">
@use '@/styles/variables' as *;

.skill-list {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-height: 0;
}

.skill-page {
  flex: 1;
  overflow-y: auto;
  padding: 8px;
  min-height: 0;
}

.skill-empty {
  padding: 24px 16px;
  font-size: 13px;
  color: $text-muted;
  text-align: center;
}

.skill-category {
  margin-bottom: 4px;
}

.category-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 6px 10px;
  border: none;
  background: none;
  color: $text-secondary;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.3px;
  cursor: pointer;
  border-radius: $radius-sm;

  &:hover {
    background: rgba(var(--accent-primary-rgb), 0.04);
  }
}

.category-arrow {
  flex-shrink: 0;
  transition: transform $transition-fast;

  &.collapsed {
    transform: rotate(-90deg);
  }
}

.category-name {
  flex: 1;
  text-align: left;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.category-description {
  padding: 0 10px 6px 28px;
  color: $text-muted;
  font-size: 11px;
  line-height: 1.4;
}

.category-count {
  font-size: 11px;
  color: $text-muted;
  background: rgba(var(--accent-primary-rgb), 0.06);
  padding: 1px 6px;
  border-radius: 8px;
}

.category-skills {
  padding: 2px 0 4px;
}

.skill-item {
  display: flex;
  flex-direction: row;
  align-items: center;
  width: 100%;
  padding: 6px 10px 6px 28px;
  border: none;
  background: none;
  color: $text-secondary;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
  border-radius: $radius-sm;
  transition: all $transition-fast;
  gap: 8px;

  &:hover {
    background: rgba(var(--accent-primary-rgb), 0.06);
    color: $text-primary;
  }

  &.active {
    background: rgba(var(--accent-primary-rgb), 0.1);
    color: $text-primary;
    font-weight: 500;
  }
}

.skill-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.skill-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}

.skill-delete {
  opacity: 0;
  transition: opacity $transition-fast;
}

.skill-item:hover .skill-delete,
.skill-item.active .skill-delete,
.skill-delete:focus-visible {
  opacity: 1;
}

.skill-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.skill-desc {
  font-size: 11px;
  color: $text-muted;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-top: 1px;
}

.skill-pagination {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 8px;
  border-top: 1px solid $border-color;
  background: $bg-card;
}

.pagination-range {
  flex-shrink: 0;
  color: $text-muted;
  font-size: 11px;
}
</style>
