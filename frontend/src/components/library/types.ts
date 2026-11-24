import type { Project } from '../../types';

export interface ProjectGroup {
  key: string;
  label: string;
  projects: Project[];
  rank: number;
}
