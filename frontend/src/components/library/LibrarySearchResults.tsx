import type * as api from '../../api/backend';
import { languageLabel } from '../../languages';
import type { SegmentSearchHit } from '../../types';
import { highlightedSnippet, libraryTimecode } from '../../utils/library';

interface Props {
  librarySearch: string;
  librarySearchHits: SegmentSearchHit[];
  librarySearchTotal: number;
  librarySearchPage: number;
  librarySearchLoading: boolean;
  librarySearchSelection: number;
  librarySearchFacets: api.SegmentSearchFacets;
  librarySearchFilters: api.SegmentSearchFilters;
  projectMatchCount: number;
  onLibrarySearchSelectionChange: (value: number | ((prev: number) => number)) => void;
  onLibrarySearchFiltersChange: (
    value:
      | api.SegmentSearchFilters
      | ((prev: api.SegmentSearchFilters) => api.SegmentSearchFilters),
  ) => void;
  onLibrarySearchPageChange: (value: number | ((prev: number) => number)) => void;
  onOpenSearchHit: (hit: SegmentSearchHit) => void;
}

export default function LibrarySearchResults({
  librarySearch,
  librarySearchHits,
  librarySearchTotal,
  librarySearchPage,
  librarySearchLoading,
  librarySearchSelection,
  librarySearchFacets,
  librarySearchFilters,
  projectMatchCount,
  onLibrarySearchSelectionChange,
  onLibrarySearchFiltersChange,
  onLibrarySearchPageChange,
  onOpenSearchHit,
}: Props) {
  return (
    <section className="library-unified-results">
      {librarySearch.trim().length === 1 ? (
        <div className="search-input-hint">
          再输入一个字即可搜索全部字幕，避免扫描整个项目库。
        </div>
      ) : (
        <>
          <details className="search-filter-panel">
            <summary>
              筛选字幕结果{' '}
              <span>{Object.values(librarySearchFilters).filter(Boolean).length || ''}</span>
            </summary>
            <div>
              <label>
                项目
                <select
                  value={String(librarySearchFilters.project_id || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      project_id: event.target.value || undefined,
                    }))
                  }
                >
                  <option value="">全部项目</option>
                  {(librarySearchFacets.projects || []).map(item => (
                    <option value={item.id} key={item.id}>
                      {item.title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                分组
                <select
                  value={String(librarySearchFilters.group_name || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      group_name: event.target.value || undefined,
                    }))
                  }
                >
                  <option value="">全部分组</option>
                  {(librarySearchFacets.groups || []).map(item => (
                    <option value={item} key={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                说话人
                <select
                  value={String(librarySearchFilters.speaker_id || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      speaker_id: event.target.value || undefined,
                    }))
                  }
                >
                  <option value="">全部说话人</option>
                  {(librarySearchFacets.speakers || []).map(item => (
                    <option value={item.id} key={item.id}>
                      {item.name} · {item.project_title}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                源语言
                <select
                  value={String(librarySearchFilters.source_language || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      source_language: event.target.value || undefined,
                    }))
                  }
                >
                  <option value="">全部</option>
                  {Array.from(
                    new Set(
                      (librarySearchFacets.projects || [])
                        .map(item => item.source_language)
                        .filter(Boolean),
                    ),
                  ).map(item => (
                    <option value={item} key={item}>
                      {languageLabel(item)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                目标语言
                <select
                  value={String(librarySearchFilters.target_language || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      target_language: event.target.value || undefined,
                    }))
                  }
                >
                  <option value="">全部</option>
                  {Array.from(
                    new Set(
                      (librarySearchFacets.projects || [])
                        .map(item => item.target_language)
                        .filter(Boolean),
                    ),
                  ).map(item => (
                    <option value={item} key={item}>
                      {languageLabel(item)}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                创建日期从
                <input
                  type="date"
                  value={String(librarySearchFilters.created_from || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      created_from: event.target.value || undefined,
                    }))
                  }
                />
              </label>
              <label>
                到
                <input
                  type="date"
                  value={String(librarySearchFilters.created_to || '')}
                  onChange={event =>
                    onLibrarySearchFiltersChange(current => ({
                      ...current,
                      created_to: event.target.value || undefined,
                    }))
                  }
                />
              </label>
              <button
                className="button secondary"
                onClick={() => onLibrarySearchFiltersChange({})}
              >
                清除筛选
              </button>
            </div>
          </details>
          <header className="search-result-heading">
            <strong>字幕命中</strong>
            <span>{librarySearchLoading ? '搜索中…' : `${librarySearchTotal} 条`}</span>
          </header>
          <div className="subtitle-search-hits">
            {librarySearchHits.map((hit, index) => (
              <button
                key={hit.segment_id}
                className={librarySearchSelection === index ? 'selected' : ''}
                onMouseEnter={() => onLibrarySearchSelectionChange(index)}
                onClick={() => onOpenSearchHit(hit)}
              >
                <span className="search-hit-time">{libraryTimecode(hit.start)}</span>
                <span>
                  <strong>{hit.project_title}</strong>
                  <small>
                    {hit.playlist_title ? `${hit.playlist_title} · ` : ''}
                    {hit.group_name ? `${hit.group_name} · ` : ''}
                    {hit.speaker_name || '未标说话人'} · 命中{' '}
                    {hit.match_fields
                      .map(field =>
                        field === 'clean_text'
                          ? '整理后原文'
                          : field === 'translated_text'
                            ? '译文'
                            : field === 'raw_text'
                              ? '原始转写'
                              : field === 'speaker_name'
                                ? '说话人'
                                : '项目标题',
                      )
                      .join('、')}
                  </small>
                  <p>{highlightedSnippet(hit.snippet, librarySearch)}</p>
                </span>
                <em>打开 ↗</em>
              </button>
            ))}
          </div>
          {!librarySearchLoading && !librarySearchHits.length && (
            <div className="search-empty">没有匹配的字幕。</div>
          )}
          {librarySearchTotal > 50 && (
            <div className="search-pagination">
              <button
                disabled={librarySearchPage <= 1}
                onClick={() => onLibrarySearchPageChange(page => page - 1)}
              >
                上一页
              </button>
              <span>
                {librarySearchPage} / {Math.ceil(librarySearchTotal / 50)}
              </span>
              <button
                disabled={librarySearchPage >= Math.ceil(librarySearchTotal / 50)}
                onClick={() => onLibrarySearchPageChange(page => page + 1)}
              >
                下一页
              </button>
            </div>
          )}
          <header className="search-result-heading project-hits-heading">
            <strong>项目命中</strong>
            <span>{projectMatchCount}</span>
          </header>
        </>
      )}
    </section>
  );
}
