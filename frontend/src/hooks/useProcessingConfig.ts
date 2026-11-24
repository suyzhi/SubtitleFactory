import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react';
import * as api from '../api/backend';
import type { ProcessingConfig, Project } from '../types';

const DEFAULT_CONFIG: ProcessingConfig = {
  model: 'auto',
  language: 'auto',
  target_language: 'zh',
  enable_clean: false,
  enable_translate: false,
  bilingual: localStorage.getItem('subtitle_factory_export_bilingual') === 'true',
  clean_target_length: 42,
};

interface UseProcessingConfigOptions {
  activeProjectIdRef: MutableRefObject<string | null>;
  setActiveProject: Dispatch<SetStateAction<Project | null>>;
  setToast: Dispatch<SetStateAction<string>>;
}

export function useProcessingConfig({
  activeProjectIdRef,
  setActiveProject,
  setToast,
}: UseProcessingConfigOptions) {
  const [config, setConfig] = useState<ProcessingConfig>(() => ({
    ...DEFAULT_CONFIG,
    enable_clean: localStorage.getItem('subtitle_factory_flow_clean') === 'true',
    enable_translate: localStorage.getItem('subtitle_factory_flow_translate') === 'true',
    clean_target_length:
      Number(localStorage.getItem('subtitle_factory_clean_target_length')) ||
      DEFAULT_CONFIG.clean_target_length,
  }));

  useEffect(() => {
    localStorage.setItem('subtitle_factory_clean_target_length', String(config.clean_target_length));
  }, [config.clean_target_length]);

  useEffect(() => {
    localStorage.setItem('subtitle_factory_flow_clean', String(config.enable_clean));
    localStorage.setItem('subtitle_factory_flow_translate', String(config.enable_translate));
  }, [config.enable_clean, config.enable_translate]);

  const flowOptions = useMemo(
    () => ({
      enable_clean: config.enable_clean,
      enable_translate: config.enable_translate,
      target_language: config.target_language,
      clean_target_length: config.clean_target_length,
      text_processing_consent: config.enable_clean || config.enable_translate,
    }),
    [
      config.clean_target_length,
      config.enable_clean,
      config.enable_translate,
      config.target_language,
    ],
  );

  const updateTargetLanguage = useCallback(
    async (targetLanguage: string) => {
      setConfig(current => ({ ...current, target_language: targetLanguage }));
      const projectId = activeProjectIdRef.current;
      if (!projectId) return;
      try {
        const updated = await api.updateProjectTargetLanguage(projectId, targetLanguage);
        if (activeProjectIdRef.current === projectId) setActiveProject(updated);
      } catch (error) {
        if (activeProjectIdRef.current === projectId) {
          setToast(error instanceof Error ? error.message : String(error));
        }
      }
    },
    [activeProjectIdRef, setActiveProject, setToast],
  );

  const updateBilingual = useCallback((bilingual: boolean) => {
    setConfig(current => ({ ...current, bilingual }));
    localStorage.setItem('subtitle_factory_export_bilingual', String(bilingual));
  }, []);

  return {
    config,
    setConfig,
    flowOptions,
    updateTargetLanguage,
    updateBilingual,
  };
}
