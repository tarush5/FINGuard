/** Shared list-screen state: pagination, sorting, debounced search, filters. */
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';

export interface Paged<T> {
  items: T[];
  pagination: {
    page: number;
    page_size: number;
    total: number;
    pages: number;
    has_next: boolean;
    has_previous: boolean;
  };
  [key: string]: unknown;
}

export function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export function useTableQuery<T>(
  key: string,
  path: string,
  options: {
    pageSize?: number;
    defaultSort?: string;
    defaultDir?: 'asc' | 'desc';
    filters?: Record<string, string | number | boolean | undefined>;
    enabled?: boolean;
    refetchInterval?: number;
  } = {},
) {
  const { pageSize = 25, defaultSort, defaultDir = 'desc', filters = {}, enabled = true, refetchInterval } = options;
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [sortBy, setSortBy] = useState<string | undefined>(defaultSort);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>(defaultDir);
  const debouncedSearch = useDebounced(search);

  const filterKey = JSON.stringify(filters);

  // Any filter or search change resets to the first page.
  useEffect(() => setPage(1), [debouncedSearch, filterKey]);

  const query = useQuery({
    queryKey: [key, page, pageSize, debouncedSearch, sortBy, sortDir, filterKey],
    queryFn: () =>
      api.get<Paged<T>>(path, {
        page,
        page_size: pageSize,
        search: debouncedSearch || undefined,
        sort_by: sortBy,
        sort_dir: sortDir,
        ...filters,
      }),
    placeholderData: keepPreviousData,
    enabled,
    refetchInterval,
  });

  const toggleSort = (field: string) => {
    if (sortBy === field) {
      setSortDir((current) => (current === 'asc' ? 'desc' : 'asc'));
    } else {
      setSortBy(field);
      setSortDir('desc');
    }
  };

  const pagination = query.data?.pagination;

  return useMemo(
    () => ({
      ...query,
      items: query.data?.items ?? [],
      payload: query.data,
      page,
      setPage,
      pages: pagination?.pages ?? 1,
      total: pagination?.total ?? 0,
      search,
      setSearch,
      sortBy,
      sortDir,
      toggleSort,
    }),
    // `toggleSort` is intentionally excluded: it is recreated every render and
    // including it would defeat the memo without changing behaviour.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [query, page, pagination, search, sortBy, sortDir],
  );
}
