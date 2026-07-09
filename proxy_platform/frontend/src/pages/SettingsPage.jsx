import { useState } from 'react'
import { useAuthStore } from '../store'
import { User, Lock, Bell, Palette, Shield, Save, Check } from 'lucide-react'

const TABS = [
  { id: 'profile', label: '个人信息', icon: User },
  { id: 'security', label: '安全设置', icon: Shield },
]

export default function SettingsPage() {
  const { user, fetchUser } = useAuthStore()
  const [activeTab, setActiveTab] = useState('profile')
  const [saved, setSaved] = useState(false)
  const [profile, setProfile] = useState({
    username: user?.username || '',
    email: user?.email || '',
  })
  const [passwords, setPasswords] = useState({
    oldPassword: '',
    newPassword: '',
    confirmPassword: '',
  })

  const handleSave = () => {
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold">设置</h1>
        <p className="text-slate-400 text-sm mt-1">管理您的账户设置和偏好</p>
      </div>

      <div className="glass-card">
        {/* Tabs */}
        <div className="flex border-b border-white/10">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setActiveTab(id)}
              className={`flex items-center gap-2 px-6 py-4 text-sm font-medium transition-colors ${
                activeTab === id
                  ? 'text-primary-400 border-b-2 border-primary-400'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              <Icon className="w-4 h-4" />
              {label}
            </button>
          ))}
        </div>

        <div className="p-6">
          {activeTab === 'profile' && (
            <div className="space-y-6 max-w-md">
              {/* Avatar */}
              <div className="flex items-center gap-4">
                <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary-500 to-purple-600 flex items-center justify-center text-3xl font-bold">
                  {user?.username?.[0]?.toUpperCase() || 'U'}
                </div>
                <div>
                  <p className="font-semibold text-lg">{user?.username}</p>
                  <p className="text-slate-400 text-sm">{user?.email || '未设置邮箱'}</p>
                  <p className="text-slate-500 text-xs mt-1">
                    注册时间: {user?.created_at ? new Date(user.created_at).toLocaleDateString('zh-CN') : '未知'}
                  </p>
                </div>
              </div>

              {/* Form */}
              <div className="space-y-4">
                <div>
                  <label className="block text-sm text-slate-400 mb-2">用户名</label>
                  <input
                    value={profile.username}
                    onChange={e => setProfile({...profile, username: e.target.value})}
                    className="input-cyber"
                    disabled
                  />
                  <p className="text-xs text-slate-500 mt-1">用户名无法修改</p>
                </div>

                <div>
                  <label className="block text-sm text-slate-400 mb-2">邮箱</label>
                  <input
                    type="email"
                    value={profile.email}
                    onChange={e => setProfile({...profile, email: e.target.value})}
                    className="input-cyber"
                    placeholder="your@email.com"
                  />
                </div>

                <button onClick={handleSave} className="btn-cyber flex items-center gap-2">
                  {saved ? <><Check className="w-4 h-4" /> 已保存</> : <><Save className="w-4 h-4" /> 保存修改</>}
                </button>
              </div>
            </div>
          )}

          {activeTab === 'security' && (
            <div className="space-y-6 max-w-md">
              <div>
                <h3 className="font-semibold mb-4">修改密码</h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm text-slate-400 mb-2">当前密码</label>
                    <input type="password" value={passwords.oldPassword} onChange={e => setPasswords({...passwords, oldPassword: e.target.value})} className="input-cyber" placeholder="输入当前密码" />
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-2">新密码</label>
                    <input type="password" value={passwords.newPassword} onChange={e => setPasswords({...passwords, newPassword: e.target.value})} className="input-cyber" placeholder="输入新密码" />
                  </div>
                  <div>
                    <label className="block text-sm text-slate-400 mb-2">确认新密码</label>
                    <input type="password" value={passwords.confirmPassword} onChange={e => setPasswords({...passwords, confirmPassword: e.target.value})} className="input-cyber" placeholder="再次输入新密码" />
                  </div>
                  <button onClick={handleSave} className="btn-cyber flex items-center gap-2">
                    {saved ? <><Check className="w-4 h-4" /> 已修改</> : <><Lock className="w-4 h-4" /> 修改密码</>}
                  </button>
                </div>
              </div>

              <div className="pt-4 border-t border-white/10">
                <h3 className="font-semibold mb-3 text-red-400">危险区域</h3>
                <button className="px-4 py-2 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 text-sm transition-colors">
                  注销账户
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}