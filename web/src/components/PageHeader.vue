<!-- 页头：标题 + 可选描述 + 右侧动作区（extra slot）；样式来自全局 .page-header 模式（styles/global.css） -->
<script setup lang="ts">
defineProps<{ title: string; desc?: string }>();
</script>

<template>
  <div class="page-header">
    <div class="page-header-main">
      <h2 class="page-header-title">{{ title }}</h2>
      <p v-if="desc" class="page-header-desc">{{ desc }}</p>
    </div>
    <div v-if="$slots.extra" class="page-header-extra">
      <slot name="extra" />
    </div>
  </div>
</template>
