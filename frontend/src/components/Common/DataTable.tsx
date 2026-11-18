import {
  type ColumnDef,
  flexRender,
  functionalUpdate,
  type PaginationState,
  type RowData,
  rowPaginationFeature,
  tableFeatures,
  useTable,
} from "@tanstack/react-table"
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react"
import { useEffect } from "react"

import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { pageSizes } from "@/lib/pagination"

const dataTableFeatures = tableFeatures({
  rowPaginationFeature,
})

export type DataTableFeatures = typeof dataTableFeatures

interface DataTableProps<TData extends RowData & { id: string }> {
  columns: ColumnDef<DataTableFeatures, TData>[]
  data: TData[]
  rowCount: number
  pagination: PaginationState
  onPaginationChange: (next: PaginationState, replace?: boolean) => void
  loading: boolean
  refreshing: boolean
}

export function DataTable<TData extends RowData & { id: string }>({
  columns,
  data,
  rowCount,
  pagination,
  onPaginationChange,
  loading,
  refreshing,
}: DataTableProps<TData>) {
  const { pageIndex, pageSize } = pagination
  const lastPageIndex = Math.max(0, Math.ceil(rowCount / pageSize) - 1)
  useEffect(() => {
    if (!refreshing && pageIndex > lastPageIndex) {
      onPaginationChange({ pageIndex: lastPageIndex, pageSize }, true)
    }
  }, [lastPageIndex, onPaginationChange, pageIndex, pageSize, refreshing])

  const table = useTable({
    features: dataTableFeatures,
    data,
    columns,
    getRowId: (row) => row.id,
    manualPagination: true,
    rowCount,
    state: { pagination },
    onPaginationChange: (update) => {
      const next = functionalUpdate(update, pagination)
      onPaginationChange({
        ...next,
        pageIndex: next.pageSize === pageSize ? next.pageIndex : 0,
      })
    },
  })

  return (
    <div className="flex flex-col gap-4">
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => {
                return (
                  <TableHead key={header.id}>
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                  </TableHead>
                )
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody aria-busy={loading}>
          {table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow key={row.id}>
                {row.getAllCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow className="hover:bg-transparent">
              <TableCell
                colSpan={columns.length}
                className="h-32 text-center text-muted-foreground"
              >
                {loading ? "Loading page…" : "No results found."}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      {rowCount > 0 && (
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 p-4 border-t bg-muted/20">
          <div className="flex flex-col sm:flex-row sm:items-center gap-4">
            <div className="text-sm text-muted-foreground" aria-live="polite">
              {data.length > 0
                ? `Showing ${pageIndex * pageSize + 1} to ${pageIndex * pageSize + data.length} of ${rowCount} entries`
                : `${rowCount} entries`}
            </div>
            <div className="flex items-center gap-x-2">
              <p className="text-sm text-muted-foreground">Rows per page</p>
              <Select
                value={`${table.state.pagination.pageSize}`}
                onValueChange={(value) => {
                  table.setPageSize(Number(value))
                }}
              >
                <SelectTrigger
                  aria-label="Rows per page"
                  className="h-8 w-[70px]"
                >
                  <SelectValue placeholder={table.state.pagination.pageSize} />
                </SelectTrigger>
                <SelectContent side="top">
                  {pageSizes.map((pageSize) => (
                    <SelectItem key={pageSize} value={`${pageSize}`}>
                      {pageSize}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex items-center gap-x-6">
            <div className="flex items-center gap-x-1 text-sm text-muted-foreground">
              <span>Page</span>
              <span className="font-medium text-foreground">
                {table.state.pagination.pageIndex + 1}
              </span>
              <span>of</span>
              <span className="font-medium text-foreground">
                {table.getPageCount()}
              </span>
            </div>

            <div className="flex items-center gap-x-1">
              <Button
                variant="outline"
                size="sm"
                className="h-8 w-8 p-0"
                onClick={() => table.setPageIndex(0)}
                disabled={!table.getCanPreviousPage()}
              >
                <span className="sr-only">Go to first page</span>
                <ChevronsLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-8 w-8 p-0"
                onClick={() => table.previousPage()}
                disabled={!table.getCanPreviousPage()}
              >
                <span className="sr-only">Go to previous page</span>
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-8 w-8 p-0"
                onClick={() => table.nextPage()}
                disabled={!table.getCanNextPage()}
              >
                <span className="sr-only">Go to next page</span>
                <ChevronRight className="h-4 w-4" />
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="h-8 w-8 p-0"
                onClick={() => table.setPageIndex(table.getPageCount() - 1)}
                disabled={!table.getCanNextPage()}
              >
                <span className="sr-only">Go to last page</span>
                <ChevronsRight className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
