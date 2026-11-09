<script setup>
import Chat from './Chat.vue'
import { canOperate } from '../auth'
import Dashboard from './Dashboard.vue'
</script>
<template><div class="page-heading"><div><div class="eyebrow">OPERATIONS OVERVIEW</div><h1>让每一次巡检，尽在掌握。</h1><p>工厂运行态势、巡检任务与智能协作，一个工作台。</p></div><div class="heading-actions"><span class="soft-badge">工厂园区 / 一层</span><router-link to="/tasks"><el-button>全部任务 ↗</el-button></router-link></div></div><div :class="['cockpit',{readonly:!canOperate}]"><section class="mapcol"><Dashboard /></section><aside v-if="canOperate" class="chatcol"><Chat /></aside></div></template>
<style scoped>.cockpit{display:grid;grid-template-columns:minmax(0,1fr) 326px;gap:20px;align-items:start}.mapcol{min-width:0}.chatcol{height:758px;min-height:0}@media(max-width:1250px){.cockpit{grid-template-columns:minmax(0,1fr) 290px;gap:14px}}@media(max-width:1000px){.cockpit{grid-template-columns:1fr}.chatcol{height:540px}}</style>
<style scoped>.cockpit.readonly{grid-template-columns:1fr}</style>
