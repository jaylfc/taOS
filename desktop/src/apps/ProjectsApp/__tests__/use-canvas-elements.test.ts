import { renderHook, act } from '@testing-library/react'
import { waitFor } from '@testing-library/react'
import { useCanvasElements } from '../canvas/use-canvas-elements'
import type { CanvasElement } from '../canvas/canvas-api'
import { canvasApi } from '../canvas/canvas-api'
import { subscribeCanvasStream } from '../canvas/canvas-sse'

vi.mock('../canvas/canvas-api')
vi.mock('../canvas/canvas-sse')

const mockListElements = canvasApi.listElements as jest.MockedFunction<typeof canvasApi.listElements>
const mockSubscribeCanvasStream = subscribeCanvasStream as jest.MockedFunction<typeof subscribeCanvasStream>

const mockUnsubscribe = vi.fn()

describe('useCanvasElements', () => {
  const projectId = 'test-project'
  const elementId = 'test-element'

  beforeEach(() => {
    vi.clearAllMocks()
    mockSubscribeCanvasStream.mockReturnValue(mockUnsubscribe)
  })

  it('loads rows from listElements', async () => {
    const mockElements: CanvasElement[] = [
      { id: '1', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u1', x: 0, y: 0, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: null, created_at: 0, updated_at: 0, deleted_at: null },
      { id: '2', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u2', x: 10, y: 10, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: null, created_at: 0, updated_at: 0, deleted_at: null },
    ]
    mockListElements.mockResolvedValue(mockElements)

    const { result } = renderHook(() => useCanvasElements(projectId))

    // Wait for the state to update to the mockElements
    await waitFor(() => {
      expect(result.current).toEqual(mockElements)
    })

    expect(mockListElements).toHaveBeenCalledWith(projectId, undefined)
  })

  it('an SSE upsert into the store reaches the returned rows', async () => {
    const initialElements: CanvasElement[] = [
      { id: '1', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u1', x: 0, y: 0, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: null, created_at: 0, updated_at: 0, deleted_at: null },
    ]
    mockListElements.mockResolvedValue(initialElements)

    const { result } = renderHook(() => useCanvasElements(projectId))
    await waitFor(() => {
      expect(result.current).toEqual(initialElements)
    })

    // Simulate an SSE message that adds a new element
    const newElement: CanvasElement = { id: '2', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u2', x: 10, y: 10, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: null, created_at: 0, updated_at: 0, deleted_at: null }

    // We need to get the store instance that was passed to subscribeCanvasStream
    // The mockSubscribeCanvasStream was called with (projectId, store, onPermissionChanged?)
    // We can capture the store argument from the mock call.
    const store = mockSubscribeCanvasStream.mock.calls[0][1]

    // Now we simulate an upsert by calling the store's upsert method with the new element
    act(() => {
      store.getState().upsert(newElement)
    })

    // Wait for the state update to propagate
    await waitFor(() => {
      expect(result.current).toEqual([initialElements[0], newElement])
    })
  })

  it('scopes rows to elementId when one is given', async () => {
    const mockElements: CanvasElement[] = [
      { id: '1', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u1', x: 0, y: 0, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: null, created_at: 0, updated_at: 0, deleted_at: null },
      { id: '2', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u2', x: 10, y: 10, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: elementId, created_at: 0, updated_at: 0, deleted_at: null },
      { id: '3', project_id: projectId, kind: 'note', author_kind: 'user', author_id: 'u3', x: 20, y: 20, w: 100, h: 50, rotation: 0, z_index: 0, payload: {}, element_id: null, created_at: 0, updated_at: 0, deleted_at: null },
    ]
    mockListElements.mockResolvedValue(mockElements)

    const { result } = renderHook(() => useCanvasElements(projectId, elementId))
    await waitFor(() => {
      expect(result.current).toEqual([mockElements[1]])
    })
  })

  it('unsubscribes the stream on unmount', async () => {
    mockListElements.mockResolvedValue([])
    const { result, unmount } = renderHook(() => useCanvasElements(projectId))
    await waitFor(() => {
      expect(result.current).toEqual([])
    })

    // The mockSubscribeCanvasStream should have been called and returned an unsubscribe function
    expect(mockSubscribeCanvasStream).toHaveBeenCalled()
    // Call unmount which should trigger the cleanup
    act(() => {
      unmount()
    })

    // The unsubscribe function returned by subscribeCanvasStream should have been called
    expect(mockUnsubscribe).toHaveBeenCalled()
  })

  it('ignores a listElements response from a superseded elementId', async () => {
    let resolveA!: (value: CanvasElement[]) => void
    let resolveB!: (value: CanvasElement[]) => void

    const promiseA = new Promise<CanvasElement[]>((resolve) => {
      resolveA = resolve
    })
    const promiseB = new Promise<CanvasElement[]>((resolve) => {
      resolveB = resolve
    })

    mockListElements
      .mockImplementationOnce(() => promiseA)
      .mockImplementationOnce(() => promiseB)

    const { result, rerender } = renderHook(
      ({ id }) => useCanvasElements(projectId, id),
      { initialProps: { id: 'a' } }
    )

    // Wait for first render (initial empty state)
    await waitFor(() => {
      expect(result.current).toEqual([])
    })

    // Switch to elementId 'b' - this triggers a new effect run
    rerender({ id: 'b' })

    // Resolve the 'b' promise first
    const bRow: CanvasElement = {
      id: 'b1',
      project_id: projectId,
      kind: 'note',
      author_kind: 'user',
      author_id: 'u1',
      x: 0,
      y: 0,
      w: 100,
      h: 50,
      rotation: 0,
      z_index: 0,
      payload: {},
      element_id: 'b',
      created_at: 0,
      updated_at: 0,
      deleted_at: null,
    }
    resolveB!([bRow])

    // Wait for the 'b' row to appear
    await waitFor(() => {
      expect(result.current).toEqual([bRow])
    })

    // Now resolve the 'a' promise (stale scope) - this should be ignored
    const aRow: CanvasElement = {
      id: 'a1',
      project_id: projectId,
      kind: 'note',
      author_kind: 'user',
      author_id: 'u1',
      x: 0,
      y: 0,
      w: 100,
      h: 50,
      rotation: 0,
      z_index: 0,
      payload: {},
      element_id: 'a',
      created_at: 0,
      updated_at: 0,
      deleted_at: null,
    }
    resolveA!([aRow])

    // Flush microtasks to let the stale response propagate through the store subscription
    await act(async () => {
      await Promise.resolve()
    })

    // Now check the final result - with the bug, this will be [] because the stale 'a'
    // response seeds the store with 'a' row, then the subscription filters for 'b' and gets []
    // After the fix, result.current should remain [bRow]
    expect(result.current).toEqual([bRow])
  })
})
