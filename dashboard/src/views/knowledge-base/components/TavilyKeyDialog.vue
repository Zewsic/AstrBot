<template>
  <v-dialog v-model="dialog" max-width="500px" persistent>
    <v-card>
      <v-card-title class="text-h3 pa-4 pb-0 pl-6">
        {{ tm('tavilyKey.title') }}
      </v-card-title>
      <v-card-text>
        <p class="mb-4 text-body-2 text-medium-emphasis">
          {{ tm('tavilyKey.descriptionBefore') }}<a href="https://tavily.com/" target="_blank">{{ tm('tavilyKey.descriptionLink') }}</a>{{ tm('tavilyKey.descriptionAfter') }}
        </p>
        <v-text-field
          v-model="apiKey"
          :label="tm('tavilyKey.keyLabel')"
          variant="outlined"
          :loading="saving"
          :error-messages="errorMessage"
          autofocus
          clearable
          placeholder="tvly-..."
        />
      </v-card-text>
      <v-card-actions>
        <v-spacer />
        <v-btn variant="text" @click="closeDialog" :disabled="saving">
          {{ tm('tavilyKey.cancel') }}
        </v-btn>
        <v-btn color="primary" variant="tonal" @click="saveKey" :loading="saving">
          {{ tm('tavilyKey.save') }}
        </v-btn>
      </v-card-actions>
    </v-card>
  </v-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { useModuleI18n } from '@/i18n/composables'
import { configProfileApi } from '@/api/v1'

const props = defineProps<{
  modelValue: boolean
}>()

const emit = defineEmits(['update:modelValue', 'success'])

const { tm } = useModuleI18n('features/knowledge-base/detail')

const dialog = ref(props.modelValue)
const apiKey = ref('')
const saving = ref(false)
const errorMessage = ref('')

watch(() => props.modelValue, (val) => {
  dialog.value = val
  if (val) {
    // Reset state when dialog opens
    apiKey.value = ''
    errorMessage.value = ''
    saving.value = false
  }
})

const closeDialog = () => {
  emit('update:modelValue', false)
}

const saveKey = async () => {
  if (!apiKey.value.trim()) {
    errorMessage.value = tm('tavilyKey.keyRequired')
    return
  }
  errorMessage.value = ''
  saving.value = true
  try {
    // 1. 获取当前配置
    const configResponse = await configProfileApi.get('default')

    if (configResponse.data.status !== 'ok') {
      throw new Error(tm('tavilyKey.loadConfigFailed'))
    }

    const currentConfig = ((configResponse.data.data as any).config || {}) as any

    // 2. 更新配置
    if (!currentConfig.provider_settings) {
      currentConfig.provider_settings = {}
    }
    currentConfig.provider_settings.websearch_tavily_key = [apiKey.value.trim()]
    // 同时将搜索提供商设置为 tavily
    currentConfig.provider_settings.websearch_provider = 'tavily'

    // 3. 保存整个配置
    const saveResponse = await configProfileApi.update('default', currentConfig)

    if (saveResponse.data.status === 'ok') {
      emit('success')
      closeDialog()
    } else {
      errorMessage.value = saveResponse.data.message || tm('tavilyKey.saveFailed')
    }
  } catch (error: any) {
    errorMessage.value = error.response?.data?.message || tm('tavilyKey.saveUnknownError')
  } finally {
    saving.value = false
  }
}
</script>
