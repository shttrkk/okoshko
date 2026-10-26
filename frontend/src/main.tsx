import React from 'react'
import { createRoot } from 'react-dom/client'

createRoot(document.getElementById('root')!).render(<React.StrictMode><main style={{fontFamily:'system-ui',padding:'10vw',background:'#faf8f5',minHeight:'70vh'}}><span style={{color:'#f15b40'}}>О!кно</span><h1>Отдых начинается<br/>с правильного номера.</h1><p>Репозиторий и контракты готовы. Интерфейс в разработке.</p><a href="/api/docs">Контракт API</a></main></React.StrictMode>)

