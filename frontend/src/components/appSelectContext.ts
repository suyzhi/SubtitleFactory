import { createContext } from 'react';

export const AppSelectThemeContext = createContext<'light' | 'dark' | undefined>(undefined);
