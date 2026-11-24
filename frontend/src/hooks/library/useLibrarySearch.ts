import { useEffect, useState } from 'react';
import * as api from '../../api/backend';
import type { SegmentSearchHit } from '../../types';

interface UseLibrarySearchOptions {
  backendStatus: 'connecting' | 'connected' | 'error';
  libraryView: 'projects' | 'trash';
}

export function useLibrarySearch({ backendStatus, libraryView }: UseLibrarySearchOptions) {
  const [librarySearch, setLibrarySearch] = useState('');
  const [librarySearchHits, setLibrarySearchHits] = useState<SegmentSearchHit[]>([]);
  const [librarySearchTotal, setLibrarySearchTotal] = useState(0);
  const [librarySearchPage, setLibrarySearchPage] = useState(1);
  const [librarySearchLoading, setLibrarySearchLoading] = useState(false);
  const [librarySearchSelection, setLibrarySearchSelection] = useState(-1);
  const [librarySearchFacets, setLibrarySearchFacets] = useState<api.SegmentSearchFacets>({});
  const [librarySearchFilters, setLibrarySearchFilters] = useState<api.SegmentSearchFilters>({});

  useEffect(() => {
    setLibrarySearchPage(1);
  }, [librarySearch, librarySearchFilters]);

  useEffect(() => {
    const query = librarySearch.trim();
    setLibrarySearchSelection(-1);
    if (backendStatus !== 'connected' || libraryView !== 'projects' || query.length < 2) {
      setLibrarySearchHits([]);
      setLibrarySearchTotal(0);
      setLibrarySearchLoading(false);
      if (!query) {
        setLibrarySearchFacets(current => (Object.keys(current).length ? {} : current));
        setLibrarySearchFilters(current => (Object.keys(current).length ? {} : current));
      }
      return;
    }
    let cancelled = false;
    setLibrarySearchLoading(true);
    const timer = window.setTimeout(() => {
      void api
        .searchSegments(query, { ...librarySearchFilters, page: librarySearchPage, page_size: 50 })
        .then(result => {
          if (cancelled) return;
          setLibrarySearchHits(result.hits);
          setLibrarySearchTotal(result.total);
          setLibrarySearchFacets(result.facets || {});
        })
        .catch(() => {
          if (!cancelled) {
            setLibrarySearchHits([]);
            setLibrarySearchTotal(0);
          }
        })
        .finally(() => {
          if (!cancelled) setLibrarySearchLoading(false);
        });
    }, 250);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [backendStatus, librarySearch, librarySearchFilters, librarySearchPage, libraryView]);

  return {
    librarySearch,
    setLibrarySearch,
    librarySearchHits,
    librarySearchTotal,
    librarySearchPage,
    setLibrarySearchPage,
    librarySearchLoading,
    librarySearchSelection,
    setLibrarySearchSelection,
    librarySearchFacets,
    librarySearchFilters,
    setLibrarySearchFilters,
  };
}
