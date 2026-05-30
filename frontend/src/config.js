// ChainWatch Frontend Configuration
// VITE_API_BASE_URL is injected at build time from .env.production
// Falls back to empty string (relative /api paths) when not set.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || '';
