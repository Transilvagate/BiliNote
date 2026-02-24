import Provider from '@/components/Form/modelForm/Provider.tsx'
import { Outlet } from 'react-router-dom'
import Options from '@/components/Form/DownloaderForm/Options.tsx'
const Downloader = () => {
  return (
    <div className={'flex h-full min-h-0 bg-white'}>
      <div className={'w-56 shrink-0 border-r border-neutral-200 p-2'}>
        <Options></Options>
      </div>
      <div className={'min-h-0 min-w-0 flex-1 overflow-y-auto'}>
        <Outlet />
      </div>
    </div>
  )
}
export default Downloader
