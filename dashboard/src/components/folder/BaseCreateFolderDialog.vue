<template>
    <v-dialog v-model="showDialog" max-width="450px">
        <v-card>
            <v-card-title class="text-h3 pa-4 pb-0 pl-6">
                <v-icon class="mr-2">mdi-folder-plus</v-icon>
                {{ mergedLabels.title }}
            </v-card-title>
            <v-card-text>
                <v-form ref="form" v-model="formValid" @submit.prevent="submitForm" :disabled="loading">
                    <v-text-field v-model="formData.name" :label="mergedLabels.nameLabel"
                        :rules="[(v: any) => !!v || mergedLabels.nameRequired]" variant="outlined"
                        density="comfortable" autofocus class="mb-3" />

                    <v-textarea v-model="formData.description" :label="mergedLabels.descriptionLabel" variant="outlined"
                        rows="3" density="comfortable" hide-details />
                </v-form>
            </v-card-text>
            <v-card-actions>
                <v-spacer />
                <v-btn variant="text" @click="closeDialog">
                    {{ mergedLabels.cancelButton }}
                </v-btn>
                <v-btn color="primary" variant="tonal" @click="submitForm" :loading="loading" :disabled="!formValid">
                    {{ mergedLabels.createButton }}
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>

<script lang="ts">
import { defineComponent, type PropType } from 'vue';
import { useI18n } from '@/i18n/composables';
import type { CreateFolderData } from './types';

const { t } = useI18n();

interface DefaultLabels {
    title: string;
    nameLabel: string;
    descriptionLabel: string;
    nameRequired: string;
    cancelButton: string;
    createButton: string;
}

const defaultLabels = (): DefaultLabels => ({
    title: t('core.shared.folder.createDialog.title'),
    nameLabel: t('core.shared.folder.createDialog.nameLabel'),
    descriptionLabel: t('core.shared.folder.createDialog.descriptionLabel'),
    nameRequired: t('core.shared.folder.createDialog.nameRequired'),
    cancelButton: t('core.shared.folder.cancelButton'),
    createButton: t('core.shared.folder.createDialog.createButton')
});

export default defineComponent({
    name: 'BaseCreateFolderDialog',
    props: {
        modelValue: {
            type: Boolean,
            default: false
        },
        parentFolderId: {
            type: String as PropType<string | null>,
            default: null
        },
        labels: {
            type: Object as PropType<Partial<DefaultLabels>>,
            default: () => ({})
        }
    },
    emits: ['update:modelValue', 'create'],
    data() {
        return {
            formValid: false,
            loading: false,
            formData: {
                name: '',
                description: ''
            }
        };
    },
    computed: {
        showDialog: {
            get(): boolean {
                return this.modelValue;
            },
            set(value: boolean) {
                this.$emit('update:modelValue', value);
            }
        },
        mergedLabels(): DefaultLabels {
            return { ...defaultLabels(), ...this.labels };
        }
    },
    watch: {
        modelValue(newValue: boolean) {
            if (newValue) {
                this.resetForm();
            }
        }
    },
    methods: {
        resetForm() {
            this.formData = {
                name: '',
                description: ''
            };
            if (this.$refs.form) {
                (this.$refs.form as any).resetValidation();
            }
        },

        closeDialog() {
            this.showDialog = false;
        },

        async submitForm() {
            if (!this.formValid) return;

            const data: CreateFolderData = {
                name: this.formData.name,
                description: this.formData.description || undefined,
                parent_id: this.parentFolderId
            };

            this.$emit('create', data);
        },

        setLoading(value: boolean) {
            this.loading = value;
        }
    }
});
</script>
