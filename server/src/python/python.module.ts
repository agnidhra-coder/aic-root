import { Global, Module } from '@nestjs/common';
import { PythonApiService } from './python-api.service';

@Global()
@Module({
  providers: [PythonApiService],
  exports: [PythonApiService],
})
export class PythonModule {}
