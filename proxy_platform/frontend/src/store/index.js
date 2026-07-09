import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import axios from 'axios'

const API_BASE = '/api'

const api = axios.create({
  baseURL: API_BASE,
  timeout: 15000,
})

// 请求拦截器 - 添加 token
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 响应拦截器 - 处理错误
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  }
)

export const useAuthStore = create(
  persist(
    (set, get) => ({
      user: null,
      token: localStorage.getItem('token'),
      isAuthenticated: !!localStorage.getItem('token'),
      
      login: async (username, password) => {
        const formData = new FormData()
        formData.append('username', username)
        formData.append('password', password)
        const { data } = await api.post('/auth/login', formData)
        localStorage.setItem('token', data.access_token)
        set({ token: data.access_token, isAuthenticated: true })
        await get().fetchUser()
        return data
      },
      
      register: async (username, email, password) => {
        const { data } = await api.post('/auth/register', { username, email, password })
        return data
      },
      
      fetchUser: async () => {
        try {
          const { data } = await api.get('/auth/me')
          set({ user: data })
        } catch (e) {
          set({ user: null, token: null, isAuthenticated: false })
        }
      },
      
      logout: () => {
        localStorage.removeItem('token')
        set({ user: null, token: null, isAuthenticated: false })
      },
    }),
    {
      name: 'auth-storage',
      partialize: (state) => ({ token: state.token }),
    }
  )
)

export const useNodeStore = create((set) => ({
  nodes: [],
  loading: false,
  error: null,
  
  fetchNodes: async () => {
    set({ loading: true, error: null })
    try {
      const { data } = await api.get('/nodes')
      set({ nodes: data, loading: false })
    } catch (e) {
      set({ error: e.message, loading: false })
    }
  },
  
  addNode: async (nodeData) => {
    const { data } = await api.post('/nodes', nodeData)
    set((state) => ({ nodes: [data, ...state.nodes] }))
    return data
  },
  
  updateNode: async (nodeId, nodeData) => {
    const { data } = await api.put(`/nodes/${nodeId}`, nodeData)
    set((state) => ({
      nodes: state.nodes.map((n) => (n.id === nodeId ? data : n)),
    }))
    return data
  },
  
  deleteNode: async (nodeId) => {
    await api.delete(`/nodes/${nodeId}`)
    set((state) => ({ nodes: state.nodes.filter((n) => n.id !== nodeId) }))
  },
}))

export const useProxyStore = create((set, get) => ({
  proxies: [],
  subscription: null,
  loading: false,
  
  fetchProxies: async () => {
    set({ loading: true })
    try {
      const { data } = await api.get('/users/proxies')
      set({ proxies: data, loading: false })
    } catch (e) {
      set({ loading: false })
    }
  },
  
  createProxy: async (nodeId, protocol) => {
    const { data } = await api.post('/users/proxies', { node_id: nodeId, protocol })
    set((state) => ({ proxies: [data, ...state.proxies] }))
    return data
  },
  
  deleteProxy: async (proxyId) => {
    await api.delete(`/users/proxies/${proxyId}`)
    set((state) => ({ proxies: state.proxies.filter((p) => p.id !== proxyId) }))
  },
  
  getProxyInfo: async (proxyId) => {
    const { data } = await api.get(`/users/proxies/${proxyId}/info`)
    return data
  },
  
  fetchSubscription: async () => {
    const { data } = await api.get('/users/subscribe')
    set({ subscription: data })
    return data
  },
}))

export default api